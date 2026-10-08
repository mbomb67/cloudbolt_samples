"""
Makes the ServiceNow ITSM "Verify connection" check honor CloudBolt's SSL
settings.

Problem:
    ``itsm.servicenow.wrapper.ServiceNowWrapper.verify_connection`` calls
    ``requests.get`` without ``verify=``. Requests then validates against the
    Python ``certifi`` bundle only, ignoring both the global SSL-verification
    preference and every CA uploaded at Admin > SSL Certificates. Behind an
    SSL-inspecting proxy, or against any instance whose chain ends in a
    private root, the check fails with::

        SSLCertVerificationError: certificate verify failed: self signed
        certificate in certificate chain

    The wrapper's real API calls (``_make_request``) pass
    ``verify=get_ssl_verification()`` and work fine, so the failure is
    confined to the check. But the check is not optional: ``itsm.forms``
    runs ``verify_connection()`` as form validation, so the ServiceNow
    connection cannot be saved from the UI, and the v3 API serializer runs it
    through ``validate_verify_connection`` too.

Fix:
    Rebind ``verify_connection`` to the same body with
    ``verify=get_ssl_verification()`` added, matching ``_make_request``. No
    other behaviour changes: same URL, auth, headers, proxy lookup, status
    handling, hibernation check, and error wrapping.

Deploy:
    Copy to PROSERV_DIR/patch_kit/patch_sets/ with the patch_kit app in
    INSTALLED_APPS (customer_settings.py), then ``systemctl restart httpd``.
    The check runs in the web process, so the job engine is not involved.

Written against the wrapper as of CloudBolt 2026-10 source. If an upgrade
changes ``verify_connection`` (for example, by adding ``verify=`` itself),
remove this patch set.
"""

import urllib.parse

import requests

from common.methods import get_proxies
from utilities.helpers import get_ssl_verification
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


def patch_servicenow_verify_connection_ssl():
    from itsm.servicenow.wrapper import ServiceNowWrapper

    if getattr(ServiceNowWrapper.verify_connection, "_ssl_verify_patch", False):
        logger.info("ServiceNow verify_connection SSL patch already applied; skipping.")
        return

    def verify_connection(self):
        """
        Verify SNOW connection.
        Return a json response.

        Patched: passes ``verify=get_ssl_verification()`` so the check uses
        CloudBolt's SSL setting and uploaded CAs, like every other request
        this wrapper makes.
        """
        try:
            auth = (self.username, self.password)
            headers = {"Accept": "application/json"}

            url = f"{self.service_now_instance_url}/api/now/table/cmdb_ci_server?sysparm_limit=1"

            netloc = urllib.parse.urlparse(url).netloc
            proxies = get_proxies(netloc)
            response = requests.get(
                url,
                auth=auth,
                headers=headers,
                proxies=proxies,
                verify=get_ssl_verification(),
            )
            logger.info(
                f"ServiceNow request returned a status code of {response.status_code}"
            )
            if response.status_code != 200:
                raise RuntimeError(
                    "Response returned the following status: {status}".format(
                        status=response.status_code
                    )
                )
            else:
                self.check_hibernation(response)

            return response
        except Exception as err:
            raise RuntimeError("Exception: {}; type: {}".format(str(err), type(err)))

    verify_connection._ssl_verify_patch = True
    ServiceNowWrapper.verify_connection = verify_connection
    logger.info("Applied ServiceNow verify_connection SSL patch.")
