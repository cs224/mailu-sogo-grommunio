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

## Leap 16 build and existing-installation upgrades

The generator still downloads the SHA-256-verified upstream archive pinned in
`gromox-container.lock.json`. The maintained `patches/gromox-core.patch` preserves
the existing split-delivery, SASL, bind-mount and optional-chat adaptations and
updates the generated image to openSUSE Leap 16.0 and its matching grommunio
repository.

The Leap 16 compatibility changes are:

- use the distribution's Python 3.13 Certbot packages and conversion modules;
- keep PHP session upload progress disabled, including after PHP is patched;
- explicitly disable TLS and certificate verification for MariaDB clients to
  preserve this bundle's existing private Docker-network database connection;
- create the package-declared runtime socket directories before supervisord;
- retain loopback-only TCP event/timer endpoints for the existing local clients;
- run the idempotent `gromox-dbop -U` schema upgrade before starting services.
- retain a valid matching persisted certificate/key pair when recreating a
  self-signed installation, so mail clients do not see a new certificate.

The database-client setting is specific to this bundle's database topology. It
must be revisited if the database is moved outside its private Docker network or
configured to require TLS.

For an existing installation, generate into a separate staging directory using
your existing configuration and secrets. Review and copy the resulting
`gromox-core` build context into the existing deployment. Preserve its
`data/grommunio`, environment files, Compose configuration and transport seed;
do not run initial provisioning again or replace them with generated defaults.

Build and recreate the core service from that deployment directory:

```bash
docker compose build --pull gromox-core
docker compose up -d --no-deps --no-build gromox-core
docker compose exec gromox-core php-fpm -v
```

Before rebuilding, retain a consistent backup of the database and mailstore.
Schema upgrades modify persisted data; restoring the previous image alone does
not undo them. Verify a vendor-confirmed patched PHP runtime before restoring
public access. In the 2026-10-23 rehearsal, Leap 16 supplied PHP/PHP-FPM
`8.4.27-160000.1.1`, fixing CVE-2026-109618 and CVE-2026-109619. Package versions
come from the distribution repositories and are not pinned by the source lock.

PHP security guidance:
https://www.php.net/archive/2026.php

### Verification on 2026-10-23

The Dockerfile and entrypoint changes were tested in an isolated recovery lab
against an existing restored installation, with external mail delivery blocked:

- database schema migration GX-132 to GX-133;
- existing mailbox hierarchy and Inbox item count;
- authenticated web/PHP-MAPI login;
- SMTP AUTH with STARTTLS, delivery and authenticated IMAP retrieval;
- CalDAV and CardDAV discovery, create, readback and delete;
- ActiveSync provisioning and FolderSync;
- Mailu/SOGo/grommunio web, health, mail protocol and recovery certificate checks.

The test image contained Gromox `3.12.251.mf230ef0`, grommunio Web
`5.2.52.gdc119f19f`, Sync `2.7.0`, and DAV `1.9.0`. Authenticated protocol tests
used a disposable account; a physical iPhone/Outlook client session was not
tested. Recovery DNS, certificates, account credentials and firewall rules are
lab concerns and are not included in the generated production build context.

Certificate-preservation regression tests run without root or Docker against
the generated context:

```bash
GROMMUNIO_TEST_CORE_DIR=/path/to/generated/gromox-core python3 -m unittest discover -s tests -v
```

They cover initial certificate generation, preservation across recreation, and
repair of a mismatched certificate/private-key pair.
