from .admin_views import MemberList, MemberRole
from django.urls import path
from .views import LogoutView, RefreshView, SignInView, change_password, get_user, request_email_change, request_username, set_password, signup, verify_email_change

"""
    Django 직접 호출은 /auth/, Nginx 경유는 /api/auth/입니다.
    
    POST /api/auth/signin
    POST /api/auth/token/refresh/
    POST /api/auth/signup/
    POST /api/auth/password/request
    POST /api/auth/password
    GET  /api/auth/user
    POST /api/auth/logout
"""
urlpatterns = [
    path("admin/members/", MemberList.as_view(), name="admin_members"),
    path("admin/members/<int:pk>/role/", MemberRole.as_view(), name="admin_member_role"),
    path("signin", SignInView.as_view(), name="login"),
    path("token/refresh/", RefreshView.as_view(), name="token_refresh"),
    path("signup/", signup, name="signup"),
    path("password/request", change_password, name="password_request"),
    path("password", set_password, name="password_reset"),
    path("user", get_user, name="auth_user"),
    path("username/request", request_username, name="username_request"),
    path("email/request", request_email_change, name="email_request"),
    path("email/verify", verify_email_change, name="email_verify"),
    path("logout", LogoutView.as_view(), name="auth_logout"),
]
