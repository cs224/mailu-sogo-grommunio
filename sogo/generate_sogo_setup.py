#!/usr/bin/env python3
"""
Generate a minimal SOGo-on-Docker-Compose setup beside Mailu.

Features:
- Reads configuration from environment variables and optionally a .env file.
- Generates random DB passwords if not provided.
- Does NOT store the mailbox login password in any generated file.
- Creates only the files needed on the SOGo host.
- Prints the exact commands to start the stack and to create/update the SOGo test user.

Usage:
  python3 generate_sogo_setup.py
  SOGO_TARGET_DIR=/opt/sogo python3 generate_sogo_setup.py
  SOGO_ENV_FILE=/root/sogo-generator.env python3 generate_sogo_setup.py

Environment variables (or .env entries):
  SOGO_TARGET_DIR=/opt/sogo
  SOGO_MAILU_NETWORK=mailu_default
  SOGO_HTTP_BIND=10.0.1.2:8888:80
  SOGO_BIND_IP=10.0.1.2
  SOGO_BIND_PORT=8888
  SOGO_EDGE_NETWORK=edge
  SOGO_EDGE_ALIAS=sogo-web
  SOGO_TZ=Europe/Berlin
  SOGO_MAIL_DOMAIN=mail.example.com
  SOGO_LOGIN_EMAIL=user@mail.example.com
  SOGO_LOGIN_CN=user
  SOGO_IMAP_URL=imap://mail.example.com:143/?tls=YES
  SOGO_IMAP_HOST=front
  SOGO_IMAP_PORT=143
  SOGO_IMAP_TLS=YES
  SOGO_SMTP_URL=smtp://mail.example.com:587/?tls=YES
  SOGO_SMTP_HOST=front
  SOGO_SMTP_PORT=587
  SOGO_SMTP_TLS=YES
  SOGO_TRUSTED_CA_FILE=./certs/internal-root-ca.crt
  SOGO_DB_IMAGE=mariadb:11.4
  SOGO_MEMCACHED_IMAGE=memcached:1.6-alpine
  SOGO_IMAGE=sonroyaalmerol/docker-sogo:5.12.6-1
  SOGO_DB_NAME=sogo
  SOGO_DB_USER=sogo
  SOGO_DB_PASSWORD=...
  SOGO_DB_ROOT_PASSWORD=...

The generated layout intentionally mirrors the reference tree in `sogo-setup/`
for the files that belong on the SOGo host:

- docker-compose.yml
- config/00-database.yaml
- config/10-mail.yaml
- config/20-auth.yaml
- database/init/01-sogo.sql
- README.txt

The Traefik dynamic config is intentionally not generated because it belongs on
the reverse-proxy/VPS host, not on the SOGo host.
"""

from __future__ import annotations

import os
import secrets
import shlex
import textwrap
from pathlib import Path


def parse_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[key] = value

    return values


def getenv(name: str, default: str, file_values: dict[str, str]) -> str:
    return os.environ.get(name, file_values.get(name, default))


def random_password(length_bytes: int = 24) -> str:
    return secrets.token_urlsafe(length_bytes)


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def write_file(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def sql_quote(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def dedent(text: str) -> str:
    return textwrap.dedent(text).lstrip()


def main() -> int:
    env_file = Path(os.environ.get("SOGO_ENV_FILE", ".env"))
    file_values = parse_dotenv(env_file)

    target_dir = Path(getenv("SOGO_TARGET_DIR", "/opt/sogo", file_values)).expanduser()
    mailu_network = getenv("SOGO_MAILU_NETWORK", "mailu_default", file_values)
    http_bind = getenv("SOGO_HTTP_BIND", "", file_values)
    bind_ip = getenv("SOGO_BIND_IP", "", file_values)
    bind_port = getenv("SOGO_BIND_PORT", "", file_values)
    edge_network = getenv("SOGO_EDGE_NETWORK", "", file_values)
    edge_alias = getenv("SOGO_EDGE_ALIAS", "", file_values)
    tz = getenv("SOGO_TZ", "Europe/Berlin", file_values)
    mail_domain = getenv("SOGO_MAIL_DOMAIN", "mail.example.com", file_values)
    login_email = getenv("SOGO_LOGIN_EMAIL", "user@mail.example.com", file_values)
    login_cn = getenv("SOGO_LOGIN_CN", "cs", file_values)

    imap_url = getenv("SOGO_IMAP_URL", "", file_values)
    imap_host = getenv("SOGO_IMAP_HOST", "front", file_values)
    imap_port = getenv("SOGO_IMAP_PORT", "143", file_values)
    imap_tls = getenv("SOGO_IMAP_TLS", "YES", file_values)

    smtp_url = getenv("SOGO_SMTP_URL", "", file_values)
    smtp_host = getenv("SOGO_SMTP_HOST", "front", file_values)
    smtp_port = getenv("SOGO_SMTP_PORT", "587", file_values)
    smtp_tls = getenv("SOGO_SMTP_TLS", "YES", file_values)
    trusted_ca_file = getenv("SOGO_TRUSTED_CA_FILE", "", file_values)

    db_image = getenv("SOGO_DB_IMAGE", "mariadb:11.4", file_values)
    memcached_image = getenv("SOGO_MEMCACHED_IMAGE", "memcached:1.6-alpine", file_values)
    sogo_image = getenv("SOGO_IMAGE", "sonroyaalmerol/docker-sogo:5.12.6-1", file_values)

    db_name = getenv("SOGO_DB_NAME", "sogo", file_values)
    db_user = getenv("SOGO_DB_USER", "sogo", file_values)
    db_password = getenv("SOGO_DB_PASSWORD", "", file_values) or random_password()
    db_root_password = getenv("SOGO_DB_ROOT_PASSWORD", "", file_values) or random_password()

    if not http_bind and bind_port:
        if bind_ip:
            http_bind = f"{bind_ip}:{bind_port}:80"
        else:
            http_bind = f"{bind_port}:80"

    if edge_alias and not edge_network:
        raise SystemExit("SOGO_EDGE_ALIAS requires SOGO_EDGE_NETWORK to be set")

    if edge_network and edge_network == mailu_network:
        raise SystemExit("SOGO_EDGE_NETWORK must differ from SOGO_MAILU_NETWORK")

    if not imap_url:
        imap_url = f"imap://{imap_host}:{imap_port}/?tls={imap_tls}"

    if not smtp_url:
        smtp_url = f"smtp://{smtp_host}:{smtp_port}/?tls={smtp_tls}"

    config_dir = target_dir / "config"
    db_init_dir = target_dir / "database" / "init"
    bootstrap_dir = target_dir / "bootstrap"
    sogo_data_dir = target_dir / "data" / "sogo"
    db_data_dir = sogo_data_dir / "db"
    state_dir = sogo_data_dir / "state"
    spool_dir = sogo_data_dir / "spool"

    for path in [config_dir, db_init_dir, db_data_dir, state_dir, spool_dir]:
        ensure_dir(path)
    if trusted_ca_file:
        ensure_dir(bootstrap_dir)

    sogo_networks = [
        "      default:",
        f"      {mailu_network}:",
    ]
    network_defs = [
        "  default:",
        "    driver: bridge",
        f"  {mailu_network}:",
        "    external: true",
    ]

    if edge_network:
        if edge_alias:
            sogo_networks.extend(
                [
                    f"      {edge_network}:",
                    "        aliases:",
                    f"          - {edge_alias}",
                ]
            )
        else:
            sogo_networks.append(f"      {edge_network}:")
        network_defs.extend(
            [
                f"  {edge_network}:",
                "    external: true",
            ]
        )

    compose_lines = [
        "services:",
        "  db:",
        f"    image: {db_image}",
        "    container_name: sogo-db",
        "    restart: unless-stopped",
        "    environment:",
        f"      MARIADB_DATABASE: {db_name}",
        f"      MARIADB_USER: {db_user}",
        f"      MARIADB_PASSWORD: {db_password}",
        f"      MARIADB_ROOT_PASSWORD: {db_root_password}",
        f"      TZ: {tz}",
        "    command:",
        "      - --character-set-server=utf8mb4",
        "      - --collation-server=utf8mb4_unicode_ci",
        "    volumes:",
        "      - ./data/sogo/db:/var/lib/mysql",
        "      - ./database/init:/docker-entrypoint-initdb.d:ro",
        "    healthcheck:",
        f'      test: ["CMD-SHELL", "mariadb-admin ping -h 127.0.0.1 -u{db_user} -p$$MARIADB_PASSWORD --silent"]',
        "      interval: 10s",
        "      timeout: 5s",
        "      retries: 20",
        "",
        "  memcached:",
        f"    image: {memcached_image}",
        "    container_name: sogo-memcached",
        "    restart: unless-stopped",
        "    command: memcached -m 128",
        "",
        "  sogo:",
        f"    image: {sogo_image}",
        "    container_name: sogo",
        "    restart: unless-stopped",
        "    depends_on:",
        "      db:",
        "        condition: service_healthy",
        "      memcached:",
        "        condition: service_started",
        "    environment:",
        f"      TZ: {tz}",
        "    volumes:",
        "      - ./config:/etc/sogo/sogo.conf.d:ro",
        "      - ./data/sogo/state:/var/lib/sogo",
        "      - ./data/sogo/spool:/var/spool/sogo",
    ]

    if trusted_ca_file:
        compose_lines.extend(
            [
                f"      - {trusted_ca_file}:/usr/local/share/ca-certificates/sogo-custom-ca.crt:ro",
                "      - ./bootstrap/sogo-entrypoint-wrapper.sh:/usr/local/bin/sogo-entrypoint-wrapper.sh:ro",
            ]
        )

    if http_bind:
        compose_lines.extend(
            [
                "    ports:",
                f'      - "{http_bind}"',
            ]
        )

    if trusted_ca_file:
        compose_lines.extend(
            [
                "    entrypoint:",
                "      - /bin/bash",
                "      - /usr/local/bin/sogo-entrypoint-wrapper.sh",
            ]
        )

    compose_lines.append("    networks:")
    compose_lines.extend(sogo_networks)
    compose_lines.append("")
    compose_lines.append("networks:")
    compose_lines.extend(network_defs)
    compose_lines.append("")

    docker_compose = "\n".join(compose_lines)

    cfg_db = dedent(
        f"""
        SOGoProfileURL: "mysql://{db_user}:{db_password}@db:3306/{db_name}/sogo_user_profile"
        OCSFolderInfoURL: "mysql://{db_user}:{db_password}@db:3306/{db_name}/sogo_folder_info"
        OCSSessionsFolderURL: "mysql://{db_user}:{db_password}@db:3306/{db_name}/sogo_sessions_folder"
        OCSStoreURL: "mysql://{db_user}:{db_password}@db:3306/{db_name}/sogo_store"
        OCSAclURL: "mysql://{db_user}:{db_password}@db:3306/{db_name}/sogo_acl"
        OCSCacheFolderURL: "mysql://{db_user}:{db_password}@db:3306/{db_name}/sogo_cache_folder"
        OCSEMailAlarmsFolderURL: "mysql://{db_user}:{db_password}@db:3306/{db_name}/sogo_alarms_folder"

        MySQL4Encoding: "utf8mb4"
        SOGoMemcachedHost: "memcached"
        """
    )

    cfg_mail = dedent(
        f"""
        SOGoPageTitle: "SOGo"
        SOGoLanguage: "German"
        SOGoTimeZone: "{tz}"

        SOGoMailDomain: "{mail_domain}"
        SOGoMailingMechanism: "smtp"
        SOGoSMTPServer: "{smtp_url}"
        SOGoSMTPAuthenticationType: "PLAIN"
        SOGoAppointmentSendEMailNotifications: true

        SOGoIMAPServer: "{imap_url}"

        SOGoDraftsFolderName: "Drafts"
        SOGoSentFolderName: "Sent"
        SOGoTrashFolderName: "Trash"
        SOGoJunkFolderName: "Junk"

        NGImap4AuthMechanism: "plain"
        NGImap4ConnectionStringSeparator: "/"

        SOGoForceExternalLoginWithEmail: true

        SOGoPasswordChangeEnabled: false
        SOGoVacationEnabled: false
        SOGoForwardEnabled: false
        SOGoSieveScriptsEnabled: false

        SOGoMailAuxiliaryUserAccountsEnabled: false
        SOGoEnablePublicAccess: false
        SOGoEnableEMailAlarms: false
        """
    )

    cfg_auth = dedent(
        f"""
        SOGoUserSources:
          - type: sql
            id: directory
            viewURL: "mysql://{db_user}:{db_password}@db:3306/{db_name}/sogo_users"
            canAuthenticate: true
            isAddressBook: true
            displayName: "SOGo Directory"
            userPasswordAlgorithm: plain
        """
    )

    db_sql = dedent(
        f"""
        CREATE DATABASE IF NOT EXISTS {db_name} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
        USE {db_name};

        CREATE TABLE IF NOT EXISTS sogo_users (
          c_uid VARCHAR(255) NOT NULL,
          c_name VARCHAR(255) NOT NULL,
          c_password VARCHAR(255) NOT NULL,
          c_cn VARCHAR(255) NOT NULL,
          mail VARCHAR(255) NOT NULL,
          PRIMARY KEY (c_uid),
          UNIQUE KEY uq_mail (mail)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """
    )

    readme = dedent(
        f"""
        This bundle contains a minimal SOGo-on-Docker-Compose setup intended to sit beside Mailu.

        Files:
        - docker-compose.yml
        - config/00-database.yaml
        - config/10-mail.yaml
        - config/20-auth.yaml
        - database/init/01-sogo.sql
        {"- bootstrap/sogo-entrypoint-wrapper.sh" if trusted_ca_file else ""}

        Before first start:
        1. Ensure Mailu network name is actually {mailu_network!r}.
        2. If you set `SOGO_EDGE_NETWORK`, ensure that external Docker network exists before starting the stack.
        3. If you set `SOGO_HTTP_BIND` or `SOGO_BIND_IP` plus `SOGO_BIND_PORT`, ensure that bind is correct for the SOGo host.
        4. If you set `SOGO_TRUSTED_CA_FILE`, ensure that file exists relative to this directory before starting the stack.
        5. Start the stack with `docker compose up -d`.
        6. Insert the SOGo login row manually with the command printed by this generator.

        This setup intentionally does NOT enable Sieve in SOGo initially.
        The reverse-proxy / Traefik config is not generated here because it belongs on the VPS host.
        """
    )

    files = {
        target_dir / "docker-compose.yml": docker_compose,
        config_dir / "00-database.yaml": cfg_db,
        config_dir / "10-mail.yaml": cfg_mail,
        config_dir / "20-auth.yaml": cfg_auth,
        db_init_dir / "01-sogo.sql": db_sql,
        target_dir / "README.txt": readme,
    }

    if trusted_ca_file:
        files[bootstrap_dir / "sogo-entrypoint-wrapper.sh"] = dedent(
            """
            #!/bin/bash
            set -euo pipefail

            update-ca-certificates >/dev/null
            exec /opt/entrypoint.sh
            """
        )

    for path, content in files.items():
        write_file(path, content)
    if trusted_ca_file:
        (bootstrap_dir / "sogo-entrypoint-wrapper.sh").chmod(0o755)

    shell_target_dir = shlex.quote(str(target_dir))
    login_email_q = sql_quote(login_email)
    login_cn_q = sql_quote(login_cn)

    print(f"Created {target_dir}")
    for path in files:
        print(f"Created {path}")

    print()
    print("Generated values:")
    print(f"  DB image:          {db_image}")
    print(f"  Memcached image:   {memcached_image}")
    print(f"  SOGo image:        {sogo_image}")
    print(f"  Mailu network:     {mailu_network}")
    print(f"  HTTP bind:         {http_bind or '(not published)'}")
    print(f"  Edge network:      {edge_network or '(not attached)'}")
    print(f"  Edge alias:        {edge_alias or '(none)'}")
    print(f"  Time zone:         {tz}")
    print(f"  Mail domain:       {mail_domain}")
    print(f"  Login email:       {login_email}")
    print(f"  Login CN:          {login_cn}")
    print(f"  IMAP URL:          {imap_url}")
    print(f"  SMTP URL:          {smtp_url}")
    print(f"  Trusted CA file:   {trusted_ca_file or '(system trust only)'}")
    print(f"  DB name:           {db_name}")
    print(f"  DB user:           {db_user}")
    print(f"  DB password:       {db_password}")
    print(f"  DB root password:  {db_root_password}")
    print()
    print("Start the stack:")
    print(f"  cd {shell_target_dir}")
    print("  docker compose up -d")
    print()
    print("Create or update the SOGo login row without storing the mailbox password in a file:")
    print(f"  cd {shell_target_dir}")
    print(f"  read -rsp \"Mailu mailbox password for {login_email}: \" MAILPW; echo")
    print(f"  docker compose exec -T db mariadb -u{db_user} -p{db_password} {db_name} <<SQL")
    print("  INSERT INTO sogo_users (c_uid, c_name, c_password, c_cn, mail)")
    print(f"  VALUES ({login_email_q}, {login_email_q}, '$MAILPW', {login_cn_q}, {login_email_q})")
    print("  ON DUPLICATE KEY UPDATE")
    print("    c_name = VALUES(c_name),")
    print("    c_password = VALUES(c_password),")
    print("    c_cn = VALUES(c_cn),")
    print("    mail = VALUES(mail);")
    print("  SQL")
    print()
    print("Update only the password later:")
    print(f"  cd {shell_target_dir}")
    print(f"  read -rsp \"New Mailu mailbox password for {login_email}: \" MAILPW; echo")
    print(f"  docker compose exec -T db mariadb -u{db_user} -p{db_password} {db_name} <<SQL")
    print("  UPDATE sogo_users")
    print("  SET c_password = '$MAILPW'")
    print(f"  WHERE c_uid = {login_email_q};")
    print("  SQL")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
