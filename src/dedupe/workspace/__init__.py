"""How the product is organised: projects, connections, uploads and runs.

    project      a named piece of work (e.g. "Customer master", "Lead clean-up"). It has its OWN steward decisions, person IDs and rules, so an
                 ad-hoc run on a test file in one project can never disturb another project's customers.
    connection   a saved source plus its field mapping (e.g. "bank_crm"), shared by all projects. Versioned and approved by a second person
                 before any run may use it. Its id is also the `source_id` stamped on every record it delivers.
    upload       a file sent from the browser (CSV, TSV, Excel), checked and stored encrypted; a connection of type `upload` points at it.
    run          one execution of a project over chosen connections. Each run writes to its own folder, so the results of earlier runs stay
                 readable while a new run is going, and any run can be reviewed later.

On disk, under the workspace root (DEDUPE_WORK_DIR):
    projects/<project>/project.db          the project's decisions and rules (development; production uses a server database per project)
    projects/<project>/identity/           the project's person-ID registry, kept across runs
    projects/<project>/runs/<run>/         one run's results
    uploads/<upload>.parquet               uploaded files, encrypted when a data key is set
"""
from .health import ConnectionHealth
from .projects import PROJECT_ID, Grant, Project, Workspace, WorkspaceError
from .runs import RUN_ID, Run, RunError, RunJobs
from .uploads import UPLOAD_ID, UploadError, Uploads

__all__ = [
    "Workspace", "Project", "Grant", "WorkspaceError", "PROJECT_ID", "RunJobs", "Run", "RunError", "RUN_ID", "Uploads", "UploadError", "UPLOAD_ID", "ConnectionHealth",
]
