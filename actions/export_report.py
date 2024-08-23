import datetime
from accounts.models import UserProfile, Group
from reportengines.internal.export_utils import (
    GroupCostServerDetailsTableConfig, GroupCostDetails, CSVWrapper)
from utilities.mail import email


def email_group_cost_server_details(recipients: list, profile: UserProfile,
                                    group: Group, start_date, end_date):
    """
    Email a report for a group.
    :param recipients: list of email addresses to send the report to
    :param profile: The CloudBolt UserProfile to run the report as
    :param group: The CloudBolt Group to run the report for
    :param start_date: The start period for the report: eg. 2024-07-01
    :param end_date: The end period for the report: eg. 2024-07-31
    :return:
    """
    # Convert the start and end dates to datetime objects.
    start_date = datetime.datetime.strptime(start_date, "%Y-%m-%d")
    end_date = datetime.datetime.strptime(end_date, "%Y-%m-%d")
    table_config, rows = group_cost_server_details(
        profile, group, start_date, end_date
    )
    if not rows:
        return "No data"
    message = 'Hello from CloudBolt'
    email_context = dict(message=message, group=group)

    attachments = []
    report = group_cost_server_details_csv(profile, group, start_date, end_date)
    attachments.append(("group_servers_report.csv", report, "text/csv"))

    email(slug='export-group-servers-report', recipients=recipients,
          context=email_context, attachments=attachments)
    return


def group_cost_server_details_csv(profile, group, start_date, end_date):
    table_config, rows = group_cost_server_details(
        profile, group, start_date, end_date, plain_text=True
    )
    if not rows:
        return "No data"

    writer = CSVWrapper()
    writer.writerow(table_config.get_column_headings())

    for row in rows:
        writer.writerow(row)

    return writer.close_and_return_as_string()


def group_cost_server_details(profile, group, start_period, end_period,
                              plain_text=False):
    """
    Export a report for a group.
    :param profile: A user profile to run the report as
    :param group: The group to run the report for
    :param start_period: The start period for the report: eg. 2024-07-01
    :param end_period: The end period for the report: eg. 2024-07-01
    :param plain_text: Whether to return the report as plain text
    :return:
    """
    # Get the server details for the group.
    table_config = GroupCostServerDetailsTableConfig(profile)
    table_config.plain_text = plain_text
    report = GroupCostDetails(group, profile, start_period, end_period)
    details = report.get_cost_details(
        table_config.get_values_query_fields(), plain_text
    )

    return table_config, table_config.get_rows(row_dicts=details)



