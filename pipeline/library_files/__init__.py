"""mr-load library_files — Drive-tree indexing for the Miraex library.

Counterpart of ic-load's pipeline/library_files. The structural difference:
ic-load read a pre-existing index out of the legacy database (bronze CSV of
Libr_* columns); here no index exists yet, so the walker *generates* it from
the replicated Drive tree (rclone lsjson manifest) and the silver layer emits
rows in the same legacy_*/libr_* shape the associativity layer expects.
"""
