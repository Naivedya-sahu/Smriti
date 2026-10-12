# Document-write mode — deferred

Smriti inks Monke's replies with **lamp** (live stylus injection → real saved
notebook ink). A second mode was prototyped: encode the reply to a reMarkable
**v6 `.rm`** page and **append it as a new page** on disk (never touching
existing pages or an imported PDF) — so it could write with the screen off, off
the page, with no pen conflict, and back a "summarise this PDF into a page at
the end" workflow.

**It works, but is shelved.** Every appended page needs a full `xochitl`
restart to render (xochitl doesn't watch the filesystem; no lighter reload
exists, and lamp-injected touch can't drive a scripted close/reopen). It is
also unsafe on a notebook that's being foreground-edited (xochitl auto-save
races the `.content` append). That makes it useless as the live-reply path.

The working engine, the v6 skeleton, the test harness, and the full findings
(coordinate mapping, block structure, the three blockers, and a revival guide)
are archived at:

    D:\Vault\Personal\Archive\Smriti-docwrite-deferred\

See `FINDINGS.md` there to revive it.
