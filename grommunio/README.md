# grommunio Files

This directory contains the public, anonymized files associated with:

- https://weisser-zwerg.dev/posts/groupware-grommunio/

It includes the production-oriented grommunio generator inputs plus the
Mailu-side static override sources needed for the split-delivery path.

Compared with the original production `scp` payload, this public bundle also
includes:

- `data-mailu-overrides-postfix/postfix.cf`
- `data-mailu-overrides-postfix/.gitignore`
- `data-mailu-overrides-rspamd/local_subnet.map`
- `data-mailu-overrides-rspamd/options.inc`

Those Mailu-side files are required to reproduce the documented split-delivery
behavior cleanly.
