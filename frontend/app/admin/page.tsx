"use client";

import Link from "next/link";
import { AdminMembersPanel } from "@/components/admin-panels";
import styles from "./page.module.css";

export default function AdminPage() {
  return <main className={`container ${styles.page}`}>
    <p className="eyebrow">ADMIN</p><h1>관리자</h1>
    <p className={styles.intro}>게시글·신고 관리는 <Link href="/mypage">마이페이지</Link>에서도 할 수 있어요. 권한이 없다는 안내가 나오면 <Link href="/login?next=admin">관리자 계정으로 로그인</Link>해 주세요.</p>
    <AdminMembersPanel />
  </main>;
}
