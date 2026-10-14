def customer_message(*, event_type: str, report_id: str, product_name: str, reference: str, status: str, qa_result: str | None) -> str:
    short = report_id[:8]
    if event_type == "bug_submitted":
        return f"Bug report {short} for {product_name} was submitted. Reference: {reference}."
    if event_type == "bug_closed":
        return f"Bug report {short} for {product_name} is closed. Reference: {reference}."
    if event_type == "bug_reopened":
        return f"Bug report {short} for {product_name} was reopened. Reference: {reference}."
    if event_type == "info_requested":
        return f"More information is needed for bug report {short} ({product_name})."
    if event_type == "qa_result":
        return f"QA result for bug report {short} ({product_name}): {qa_result}."
    if event_type == "status_changed":
        return f"Bug report {short} for {product_name} status is now {status}."
    return f"Update for bug report {short} ({product_name})."


def developer_message(
    *,
    event_type: str,
    product_name: str,
    severity: str,
    customer_name: str,
    reference: str,
    title: str,
    description: str,
) -> str:
    summary = " ".join(description.split())
    if len(summary) > 400:
        summary = summary[:397] + "..."
    return (
        f"{event_type} for {product_name}. Severity {severity}. Customer {customer_name}. "
        f"Issue {reference}. {title}. {summary}"
    )


def management_message(
    *,
    event_type: str,
    report_id: str,
    product_name: str,
    team_name: str,
    severity: str,
    product_open: int,
    team_open: int,
) -> str:
    short = report_id[:8]
    if event_type == "bug_closed":
        lead = f"Fixed bug {short} on {product_name}, team {team_name}."
    elif event_type == "bug_reopened":
        lead = f"Reopened bug {short} on {product_name}, team {team_name}."
    else:
        lead = f"New bug {short} on {product_name} ({severity}), team {team_name}."
    return (
        f"{lead} Open issues for {product_name}: {product_open}. "
        f"Open issues for team {team_name}: {team_open}."
    )
