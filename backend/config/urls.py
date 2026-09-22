"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.http import JsonResponse
from django.urls import path, include
from drf_spectacular.views import SpectacularAPIView
from llm.views import (
    ChatFinalizeView,
    ChatMessageView,
    ChatRoomDetailView,
    ChatRoomView,
    ChatTurnListView,
    GuestChatView,
)


def health_check(request):
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("healthz/", health_check, name="health-check"),
    path("schema/", SpectacularAPIView.as_view(), name="api-schema"),
    path("admin/", admin.site.urls),
    path("", include("travel.urls")),
    path("community/", include("community.urls")),
    path("chat/sessions/", ChatRoomView.as_view()),

    path(
        "chat/sessions/<int:session_id>/",
        ChatRoomDetailView.as_view(),
    ),

    path(
        "chat/sessions/<int:session_id>/messages/",
        ChatMessageView.as_view(),
    ),
    path("chat/sessions/<int:session_id>/turns/", ChatTurnListView.as_view()),
    path("chat/turns/<uuid:turn_id>/finalize/", ChatFinalizeView.as_view()),
    path("chat/guest/", GuestChatView.as_view()),
    path('auth/',include('accounts.urls')),
    path('baseball/', include('baseball.urls')),
    path('tving/', include('tving.urls')),
]
