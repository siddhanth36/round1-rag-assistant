from django.contrib import admin
from django.urls import include, path

from backend.accounts import views as accounts
from backend.assistant import views as assistant
from backend.inspections import views as inspections
from backend.machines import views as machines

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/auth/csrf/", accounts.csrf),
    path("api/auth/login/", accounts.login_view),
    path("api/auth/logout/", accounts.logout_view),
    path("api/auth/me/", accounts.me),
    path("api/machines/", machines.MachineList.as_view()),
    path("api/manuals/", machines.ManualList.as_view()),
    path("api/manuals/upload/", machines.upload_manual),
    path("api/manuals/<int:pk>/", machines.ManualDetail.as_view()),
    path("api/manuals/<int:pk>/page/<int:page>/", machines.manual_page),
    path("api/manuals/<int:pk>/file/", machines.manual_file),
    path("api/assistant/ask/", assistant.ask),
    path("api/history/", assistant.History.as_view()),
    path("api/supervisor/questions/", assistant.SupervisorQuestions.as_view()),
    path("api/supervisor/analytics/", assistant.analytics),
    path("api/products/", inspections.ProductList.as_view()),
    path("api/products/<str:product_id>/", inspections.ProductDetail.as_view()),
    path("api/inspections/", inspections.create_inspection),
    path("api/inspections/<int:pk>/", inspections.InspectionDetail.as_view()),
    path("api/inspections/<int:pk>/image/", inspections.inspection_image),
    path("", include("backend.inspections.pages")),  # Round 2 pages (frontend teammate)
    path("", include("backend.config.pages")),
]
