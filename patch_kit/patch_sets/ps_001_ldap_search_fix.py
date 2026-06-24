import ldap
from ldap.controls import SimplePagedResultsControl

from utilities.logger import ThreadLogger
from utilities.models import LDAPUtility

logger = ThreadLogger(__name__)

def patch_ldap_search():

    def verify_settings(self):
        """
        After verifying connection, attempt to perform a simple test search on
        this LDAPUtility. Raises exceptions on critical errors and may return a
        list of warning strings for other problems or potential problems.
        """
        self._create_binding()
        base_error = "Could not perform a test search: "
        try:
            # Run a test search:
            warnings = self.runTestSearch()
        except ldap.UNWILLING_TO_PERFORM:
            raise RuntimeError(
                base_error + '" permissions error. Please verify the credentials using '
                             "Edit Credentials or Verify Connection."
            )
        except ldap.NO_SUCH_OBJECT:
            raise RuntimeError(
                base_error
                + '"No such object". Please verify the Account and Base DN fields.'
            )
        except ldap.FILTER_ERROR:
            raise RuntimeError(
                base_error
                + '"Bad search filter". Please verify the value of the ldap filter field.'
            )
        except ldap.SIZELIMIT_EXCEEDED:
            pass
        except Exception as err:
            raise RuntimeError(
                base_error + "Exception: {}; type: {}".format(str(err),
                                                              type(err))
            )
        else:
            return warnings

    def runUserSearch(self, username=None, find=None):
        """
        Use sudo to run a filter for the given username and return the
        requested results (by default only "dn").

        Returns an empty list if the user is not found, or <None> if there is
        a problem contacting the server.
        """
        if find is None:
            find = ["dn"]

        auth_filter = "(%s=%s)" % (self.ldap_username, username)
        try:
            return self.runSearch(auth_filter, find, all=0, page_size=0)
        except Exception as e:
            logger.debug(e)
            return None

    LDAPUtility.runUserSearch = runUserSearch
    LDAPUtility.verify_settings = verify_settings
