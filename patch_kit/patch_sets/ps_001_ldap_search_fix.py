import ldap
from ldap.controls import SimplePagedResultsControl

from utilities.logger import ThreadLogger
from utilities.models import LDAPUtility

logger = ThreadLogger(__name__)

def patch_ldap_search():

    def runTestSearch(self, page_size=0):
        """
        Similar to getUserList(), except that it's designed for a quick
        connection check to make sure searches can be run with the
        given settings for the LDAPUtility.

        Will raise an exception a critical error (queries failed); otherwise
        will return a list of warning strings, if any.
        """

        warnings = []

        find = ["dn", self.ldap_username, self.ldap_first, self.ldap_last]
        if self.ldap_mail:
            find.append(self.ldap_mail)

        auth_filter = "(objectClass=person)"
        data = self.runSearch(auth_filter, find, all=1, sizelimit=1,
                              page_size=0)
        if len(data) == 0:
            warnings.append("No users returned with given settings.")
        else:
            a_user = data[0][1]
            for field in find[1:]:  # Except "dn"
                if field not in a_user:
                    # What does this mean? More user guidance would be good.
                    warnings.append(
                        "Field '{}' missing from results.".format(field))

        # Test the "disabled_filter" field:
        if self.disabled_filter:
            try:
                filter = "(&(objectClass=person)(%s))" % self.disabled_filter
                data = self.runSearch(
                    filter, [self.ldap_username], all=1, sizelimit=1,
                    page_size=0
                )
            except ldap.FILTER_ERROR:
                data = []
            if len(data) == 0:
                warnings.append(
                    "No disabled users were returned. This may be okay or an "
                    "indication of a problem with the Disabled User Filter field."
                )

        return warnings

    def runSearch(
            self,
            filter,
            find,
            all=0,
            sizelimit=0,
            logger=None,
            page_size=100,
            ignore_utility_filter=False,
    ):
        """
        Use sudo to run a given search, and return the requested results.
        For documentation for the "all" option, see:
        http://www.python-ldap.org/doc/html/ldap.html#functions

        Set page_size > 0 to use RFC 2696 paged results (recommended for large
        directories to avoid server-side size-limit errors).
        NOTE: ignore_utility_filter is used to allow setting the filter to exactly what your filter is passed in as,
              versus combining filter and ldap_utility.ldap_filter

              The ignore_utility_filter should generally not be set to True for CloudBolt backend user searches (default behavior).
              However, when using this method for CloudBolt plugins you write, you typically don't want the ldap_filter to
              get added to the filter you pass to this function because that will alter the result-set you are expecting back.

        NOTE: the `all` parameter is only meaningful when page_size=0. When
              paging is active (the default), result3() always collects all entries
              per page internally, so passing all=0 or all=1 has no effect.
        """
        if not logger:
            logger = ThreadLogger(__name__)

        try:
            su = self._init_ldap_sudo()
            if self.ldap_filter and not ignore_utility_filter:
                filter = "(&(%s)%s)" % (self.ldap_filter, filter)

            log_cmd = 'ldapsearch -H {}://{}:{} -P {} -b "{}" -D "{}" -w "<password>" -x{} "{}"'
            paging_flag = (
                " -E pr={}/noprompt".format(page_size) if page_size > 0 else ""
            )
            logger.debug(
                log_cmd.format(
                    self.protocol,
                    self.ip,
                    self.port,
                    self.protocol_version,
                    self.base_dn,
                    self.serviceaccount,
                    paging_flag,
                    filter,
                )
            )
            if page_size > 0:
                # get the data in pages so that we don't submit a search that exceeds the server's size limit
                page_control = SimplePagedResultsControl(
                    True, size=page_size, cookie=""
                )
                paged_ctrl_type = SimplePagedResultsControl.controlType
                base_dn = self.base_dn
                scope = ldap.SCOPE_SUBTREE
                data = []
                while True:
                    msgid = su.search_ext(
                        base_dn,
                        scope,
                        filter,
                        find,
                        sizelimit=sizelimit,
                        serverctrls=[page_control],
                    )
                    status, rdata, _, serverctrls = su.result3(msgid)
                    data.extend(rdata)
                    page_ctrl = next(
                        (c for c in serverctrls if
                         c.controlType == paged_ctrl_type),
                        None,
                    )
                    if page_ctrl and page_ctrl.cookie:
                        page_control.cookie = page_ctrl.cookie
                    else:
                        break
            else:
                # get the entire resultset as a single data set
                status, data = su.result(
                    # python-ldap allows utf8, but expects bytestrings so we have
                    # to encode it manually
                    # https://github.com/pyldap/pyldap/issues/35
                    su.search_ext(
                        self.base_dn,
                        ldap.SCOPE_SUBTREE,
                        filter,
                        find,
                        sizelimit=sizelimit,
                    ),
                    all,
                )

            # For some reason, the code we get is offset by 97 from the LDAP
            # status code.
            logger.debug("LDAP status code: {}".format(status - 97))
            logger.debug("LDAP data: {}".format(data))
            su.unbind()

            # Drop AD search-continuation references: when the search base is a
            # domain/forest root, AD returns (None, [referral-uri]) tuples
            # alongside real entries. They are not directory objects and would
            # otherwise inflate result counts (e.g. break the len == 1 check in
            # ldap_user_attributes) and fail user lookups.
            data = [entry for entry in data if entry[0] is not None]

            # Convert bytestrings in data to text before returning
            from common.methods import bytes_to_text

            return bytes_to_text(data)
        except Exception:
            # this call will include the traceback below
            logger.exception("Error during LDAP search:")
            raise

    LDAPUtility.runTestSearch = runTestSearch
    LDAPUtility.runSearch = runSearch
