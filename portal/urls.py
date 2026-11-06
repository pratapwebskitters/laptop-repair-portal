from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from repairs import api, views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("login/", auth_views.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", views.dashboard, name="dashboard"),
    path("tickets/new/", views.intake, name="intake"),
    path("tickets/<int:pk>/", views.detail, name="ticket"),
    path("track/<str:token>/", views.track, name="track"),
    path("api/v1/tickets/", api.TicketList.as_view()),
    path("api/v1/tickets/<int:pk>/", api.TicketDetail.as_view()),
    path("api/v1/tickets/<int:pk>/status/", api.ChangeStatus.as_view()),
    path("api/v1/tickets/<int:pk>/notes/", api.CreateNote.as_view()),
    path("api/v1/tickets/<int:pk>/tracking-link/", api.RotateLink.as_view()),
    path("api/v1/track/<str:token>/", api.PublicTracking.as_view()),
]
