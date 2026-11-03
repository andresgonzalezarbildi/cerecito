# Cerecito

> **A local accounting automation tool that combines browser automation with fiscal validation and human review instead of blindly entering data.**

Cerecito grew out of a repetitive administrative workflow: receiving electronic invoices, identifying how they should be recorded, checking whether they already exist and manually entering them into Zeta. My goal was to automate the predictable work while keeping uncertain accounting decisions visible and reviewable.

## How It's Made

**Tech used:** Python, Playwright, SQLite, JavaScript, HTML, CSS, XML, openpyxl

The application runs as a local web interface backed by Python. It imports Uruguayan electronic invoices (CFE) from XML files or ZIP archives, extracts their fiscal identity and line items, and organizes them into a persistent review workflow.

SQLite stores imported documents, original lines, catalog mappings, manual decisions, learned rules and execution history. Playwright connects to a dedicated Chrome session to inspect and interact with the existing Zeta web interface. The code separates document parsing, reconciliation, user decisions and browser execution into different modules.

## Engineering Approach

The central rule is that uncertainty should stop an operation, not silently produce an accounting entry. The parser uses decimal arithmetic for monetary calculations, preserves original XML information and checks tax indicators, totals and document identity.

Before attempting to save, the workflow checks for duplicates and validates supplier and destination mappings against imported catalogs. A manual correction can become a narrowly scoped rule for future documents, but an unverified suggestion is not treated as a confirmed match. Background jobs, locks and operation journals help prevent overlapping work and accidental retries.

## Optimizations

I moved configuration and company data outside the repository, added database migrations and made the workflow resumable across restarts. Automated tests cover parsing, persistence, duplicate detection, user decisions and simulated browser execution.

**Current status:** The verified credit-purchase flows have browser adapters; cash purchases and more complex fiscal documents remain blocked pending real-system validation. This project taught me to treat reliability, traceability and recoverability as first-class requirements in automation, not as features added after it works once.
