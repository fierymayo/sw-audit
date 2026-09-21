#!/usr/bin/env python3
"""One-command full audit: fresh products + collections (members + deliverable)."""
import audit
import collections_audit

if __name__ == "__main__":
    audit.pipeline(pdf=True)
    collections_audit.pipeline(members_pull=True, deliverable=True)
