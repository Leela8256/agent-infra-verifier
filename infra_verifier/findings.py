"""Keep hard failures distinct from soft quality warnings."""


def failure_tags(report):
    hard, soft = set(), set()
    for check in report.get("quality", {}).get("checks", []) + report.get(
        "infra_checks", []
    ):
        if check.get("result") == "fail":
            (hard if check.get("hard", True) else soft).add(check["tag"])
    if report.get("exit_code") == 0 and report["quality"]["verdict"] == "fail":
        hard.add("said_done_but_failed")
    # A tag can occur at both severities; hard takes precedence in the CSV.
    return sorted(hard), sorted(soft - hard)
