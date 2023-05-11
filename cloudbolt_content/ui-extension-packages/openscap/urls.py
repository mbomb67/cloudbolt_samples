from django.conf.urls import url
from . import views

xui_urlpatterns = [
    url(r'^servers/(?P<server_id>\d+)/run_evaluation/$',
        views.run_evaluation,
        name='run_evaluation'),
]
