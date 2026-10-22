"""Explicit one-time migration of existing app expense metadata. No vendor calls."""
from swapmein.finance import amount, record_expense, replicate_cost, scan
from swapmein.storage import Records


def backfill(records):
    jobs, partial = scan(records, "jobs/")
    if partial:
        raise RuntimeError("Job history exceeds migration limit; use a paged migration before proceeding")
    count = 0
    for job in jobs:
        if job.get("access_mode") == "credits" and job.get("provider_task") and job.get("provider_cost_recorded_at"):
            cost = amount(job.get("provider_billing", {}).get("provider_cost_usd"))
            record_expense(records, job.get("provider", "reAPI"), job["provider_task"],
                at=job["provider_cost_recorded_at"], cost=cost,
                status="reported" if cost is not None else "unavailable", job_id=job.get("id"), detail="Video generation")
            count += 1
        for operation, state in job.get("replicate_predictions", {}).items():
            if state.get("id"):
                cost = replicate_cost(state)
                record_expense(records,"Replicate",state["id"],at=state.get("submitted_at",job.get("created_at")),
                    cost=cost,status="estimated" if cost is not None else "unavailable",
                    job_id=job.get("id"),detail=f"{operation}: runtime × public hardware rate (2026-10-22)")
                count += 1
    return count


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--bucket", required=True)
    args = parser.parse_args()
    print({"processed_expenses": backfill(Records(args.bucket))})
