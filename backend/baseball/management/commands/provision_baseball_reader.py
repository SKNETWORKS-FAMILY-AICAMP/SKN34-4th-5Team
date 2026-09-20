from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, connections, transaction
from psycopg import sql

from baseball.query_repository import BaseballQueryRepository


class Command(BaseCommand):
    help = "야구 SQL 조회 전용 역할을 점검하고 19개 테이블 SELECT 권한만 부여합니다."

    def add_arguments(self, parser):
        parser.add_argument(
            "--prepare-db-permissions",
            action="store_true",
            help="현재 DB 소유자의 TEMP 권한을 보존하고 PUBLIC의 TEMP 권한만 제거합니다.",
        )

    def handle(self, *args, **options):
        owner = settings.DATABASES["default"]
        reader = settings.DATABASES["baseball_readonly"]
        role, password = reader["USER"], reader["PASSWORD"]
        if not role or not password:
            raise CommandError("BASEBALL_DB_USER와 BASEBALL_DB_PASSWORD가 필요합니다.")
        if role == owner["USER"]:
            raise CommandError("조회 역할은 기본 DB 소유자와 달라야 합니다.")

        tables = [model._meta.db_table for model in BaseballQueryRepository.models()]
        try:
            with transaction.atomic(using="default"):
                with connections["default"].cursor() as cursor:
                    if options["prepare_db_permissions"]:
                        self._prepare_db_permissions(cursor, owner["USER"])
                    role_oid = self._audit_role(cursor, role, tables)
                    self._audit_public(cursor, tables)
                    self._audit_default_acl(cursor, role_oid)
                    role_id = sql.Identifier(role)
                    action = "ALTER" if role_oid else "CREATE"
                    cursor.execute(
                        sql.SQL(
                            f"{action} ROLE {{}} LOGIN PASSWORD {{}} NOSUPERUSER NOCREATEDB "
                            "NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT"
                        ).format(role_id, sql.Literal(password))
                    )
                    cursor.execute(sql.SQL("ALTER ROLE {} SET default_transaction_read_only = on").format(role_id))
                    cursor.execute(sql.SQL("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM {}").format(role_id))
                    cursor.execute(sql.SQL("REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM {}").format(role_id))
                    cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role_id))
                    cursor.execute(
                        sql.SQL("GRANT SELECT ON TABLE {} TO {}").format(
                            sql.SQL(", ").join(map(sql.Identifier, tables)), role_id
                        )
                    )
        except DatabaseError:
            raise CommandError("조회 역할 설정에 실패해 모든 변경을 롤백했습니다.") from None
        self.stdout.write(self.style.SUCCESS(f"{role}: 야구 읽기 전용 권한 설정 완료"))

    @staticmethod
    def _prepare_db_permissions(cursor, owner):
        cursor.execute("SELECT current_database()")
        database = cursor.fetchone()[0]
        cursor.execute(
            sql.SQL("GRANT TEMPORARY ON DATABASE {} TO {}").format(
                sql.Identifier(database), sql.Identifier(owner)
            )
        )
        cursor.execute(
            sql.SQL("REVOKE TEMPORARY ON DATABASE {} FROM PUBLIC").format(
                sql.Identifier(database)
            )
        )

    @staticmethod
    def _audit_role(cursor, role, tables):
        cursor.execute(
            """SELECT oid, rolsuper, rolcreatedb, rolcreaterole, rolreplication,
                      rolbypassrls, rolinherit, rolconfig FROM pg_roles WHERE rolname = %s""",
            [role],
        )
        state = cursor.fetchone()
        if not state:
            return None
        role_oid = state[0]
        if any(state[1:7]):
            raise CommandError("기존 조회 역할에 위험한 role 속성이 있어 재사용할 수 없습니다.")
        if state[7] not in (None, ["default_transaction_read_only=on"]):
            raise CommandError("기존 조회 역할에 허용되지 않은 role 설정이 있습니다.")
        cursor.execute(
            "SELECT 1 FROM pg_auth_members WHERE roleid = %s OR member = %s LIMIT 1",
            [role_oid, role_oid],
        )
        if cursor.fetchone():
            raise CommandError("기존 조회 역할에 role 멤버십이 있어 재사용할 수 없습니다.")
        cursor.execute(
            """SELECT 1 WHERE
                 EXISTS (SELECT 1 FROM pg_database WHERE datdba = %s) OR
                 EXISTS (SELECT 1 FROM pg_namespace WHERE nspowner = %s) OR
                 EXISTS (SELECT 1 FROM pg_class WHERE relowner = %s) OR
                 EXISTS (SELECT 1 FROM pg_proc WHERE proowner = %s)""",
            [role_oid] * 4,
        )
        if cursor.fetchone():
            raise CommandError("기존 조회 역할이 DB 객체를 소유해 재사용할 수 없습니다.")
        cursor.execute(
            """SELECT 1 FROM pg_database d
               CROSS JOIN LATERAL aclexplode(COALESCE(d.datacl, acldefault('d', d.datdba))) a
               WHERE d.datname = current_database() AND a.grantee = %s
                 AND a.privilege_type IN ('CREATE', 'TEMPORARY') LIMIT 1""",
            [role_oid],
        )
        if cursor.fetchone():
            raise CommandError("기존 조회 역할에 DB CREATE/TEMP 권한이 있어 재사용할 수 없습니다.")
        cursor.execute(
            """SELECT 1 FROM pg_namespace n
               CROSS JOIN LATERAL aclexplode(COALESCE(n.nspacl, acldefault('n', n.nspowner))) a
               WHERE a.grantee = %s AND n.nspname !~ '^pg_'
                 AND n.nspname <> 'information_schema'
                 AND (n.nspname <> 'public' OR a.privilege_type <> 'USAGE') LIMIT 1""",
            [role_oid],
        )
        if cursor.fetchone():
            raise CommandError("기존 조회 역할에 허용되지 않은 schema 권한이 있습니다.")
        if Command._has_unsafe_relation_acl(cursor, role_oid, tables):
            raise CommandError("기존 조회 역할에 허용되지 않은 relation/column 권한이 있습니다.")
        cursor.execute(
            """SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
               CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl, acldefault('f', p.proowner))) a
               WHERE a.grantee = %s AND n.nspname !~ '^pg_'
                 AND n.nspname <> 'information_schema' LIMIT 1""",
            [role_oid],
        )
        if cursor.fetchone():
            raise CommandError("기존 조회 역할에 직접 함수 실행 권한이 있습니다.")
        return role_oid

    @staticmethod
    def _audit_public(cursor, tables):
        cursor.execute(
            """SELECT 1 FROM pg_database d
               CROSS JOIN LATERAL aclexplode(COALESCE(d.datacl, acldefault('d', d.datdba))) a
               WHERE d.datname = current_database() AND a.grantee = 0
                 AND a.privilege_type IN ('CREATE', 'TEMPORARY') LIMIT 1"""
        )
        if cursor.fetchone():
            raise CommandError("PUBLIC에 DB CREATE/TEMP 권한이 있습니다. 공용 권한은 자동 변경하지 않습니다.")
        cursor.execute(
            """SELECT 1 FROM pg_namespace n
               CROSS JOIN LATERAL aclexplode(COALESCE(n.nspacl, acldefault('n', n.nspowner))) a
               WHERE a.grantee = 0 AND a.privilege_type = 'CREATE'
                 AND n.nspname !~ '^pg_' AND n.nspname <> 'information_schema' LIMIT 1"""
        )
        if cursor.fetchone():
            raise CommandError("PUBLIC에 schema CREATE 권한이 있습니다. 공용 권한은 자동 변경하지 않습니다.")
        if Command._has_unsafe_relation_acl(cursor, 0, tables):
            raise CommandError("PUBLIC에 허용되지 않은 relation/column 권한이 있습니다. 공용 권한은 자동 변경하지 않습니다.")
        cursor.execute(
            """SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
               CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl, acldefault('f', p.proowner))) a
               WHERE a.grantee = 0 AND p.prosecdef AND a.privilege_type = 'EXECUTE'
                 AND n.nspname !~ '^pg_' AND n.nspname <> 'information_schema' LIMIT 1"""
        )
        if cursor.fetchone():
            raise CommandError("PUBLIC 실행 가능한 SECURITY DEFINER 함수가 있습니다. 공용 권한은 자동 변경하지 않습니다.")

    @staticmethod
    def _has_unsafe_relation_acl(cursor, grantee, tables):
        cursor.execute(
            """SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
               CROSS JOIN LATERAL aclexplode(
                 COALESCE(c.relacl, acldefault(CASE WHEN c.relkind = 'S' THEN 'S'::"char" ELSE 'r'::"char" END, c.relowner))
               ) a
               WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
                 AND a.grantee = %s
                 AND (n.nspname <> 'public' OR c.relname <> ALL(%s) OR a.privilege_type <> 'SELECT')
               LIMIT 1""",
            [grantee, tables],
        )
        if cursor.fetchone():
            return True
        cursor.execute(
            """SELECT 1 FROM pg_attribute att JOIN pg_class c ON c.oid = att.attrelid
               JOIN pg_namespace n ON n.oid = c.relnamespace
               CROSS JOIN LATERAL aclexplode(att.attacl) a
               WHERE att.attacl IS NOT NULL AND a.grantee = %s
                 AND n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
                 AND (n.nspname <> 'public' OR c.relname <> ALL(%s) OR a.privilege_type <> 'SELECT')
               LIMIT 1""",
            [grantee, tables],
        )
        return cursor.fetchone() is not None

    @staticmethod
    def _audit_default_acl(cursor, role_oid):
        grantees = [0] if role_oid is None else [0, role_oid]
        cursor.execute(
            """SELECT 1 FROM pg_default_acl d
               CROSS JOIN LATERAL aclexplode(d.defaclacl) a
               WHERE d.defaclobjtype IN ('r', 'S', 'n', 'f')
                 AND a.grantee = ANY(%s) LIMIT 1""",
            [grantees],
        )
        if cursor.fetchone():
            raise CommandError(
                "PUBLIC 또는 조회 역할에 위험한 default privilege가 있습니다. "
                "공용 정책은 자동 변경하지 않습니다."
            )
