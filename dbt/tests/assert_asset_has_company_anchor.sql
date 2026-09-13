{{ config(severity='warn') }}
-- Card warn_check: files in scope without a company anchor are surfaced for
-- the operator (they sit in silver_library_orphans) but do not block the run.
select node_key, node_name, legacy_file_path
from {{ ref('silver_library_orphans') }}
