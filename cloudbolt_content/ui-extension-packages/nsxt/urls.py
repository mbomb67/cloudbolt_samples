from django.conf.urls import url
from xui.nsxt.views import add_security_tag, remove_security_tag

xui_urlpatterns = [
    url(
        r"^add_security_tag/(?P<server_id>\d+)/$",
        add_security_tag,
        name="add_security_tag",
    ),
    url(
        r"^remove_security_tag/(?P<server_id>\d+)/tag/(?P<tag_name>[\w\-]+)/$",
        remove_security_tag,
        name="remove_security_tag",
    ),
]
