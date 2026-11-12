# Preparing public source

Public defaults contain no site URLs. Keep databases, authentication files,
environment files and site-specific `settings.json` outside the source tree as
described in [STORAGE.md](STORAGE.md). DokuWiki links are disabled until configured
for your own site. Billing regression fixtures use fictional financial records.

The private development repository's older commits contain installation details
and account-specific maintenance data. Cleaning the latest files does not remove
those older versions or the personal metadata in commits. Keep that history
private when creating a public repository.

For a first public release, start a new repository from a reviewed source-only
snapshot with no `.git` directory. Do not copy the whole development working
directory: ignored databases, editor files, credentials and backups may be present.
Commit the cleanup privately before using `git archive HEAD` to create such a
snapshot; that command exports committed files, not uncommitted changes. Inspect
the archive before publishing, and choose the name/email for its new initial
commit deliberately.

The ignore rules are a guard against accidental additions. They do not remove
files already tracked by Git, and they do not sanitize earlier commits. Review
future release changes for personal data as well as credentials.
