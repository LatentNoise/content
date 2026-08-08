# output/

The example scripts download the artifacts they produce here, one subdirectory
per job (`output/<job_id>/`). The contents are not versioned — safe to empty: the
truth stays in the API (`GET /api/v1/jobs`, `GET /api/v1/artifacts/{id}/content`).
