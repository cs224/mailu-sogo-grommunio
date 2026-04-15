#!/usr/bin/env python3
"""
Generate a minimal grommunio-on-Docker-Compose setup beside Mailu.

Features:
- Reads configuration from environment variables and optionally a .env file.
- Generates random passwords if not provided.
- Uses compose-relative persistence under ./data/grommunio.
- Uses a commit-pinned gromox-container archive and extracts gromox-core.
- Fetches the pinned archive only if the local cached tarball is missing.
- Renders the surrounding bundle from repo-managed Jinja2 templates.
- Applies only the repo-managed gromox-core patch onto upstream.
- Creates only the files needed on the grommunio host.
- Applies a repo-managed Postfix transport seed from ./postfix/transport.seed.

Usage:
  python3 generate_grommunio_setup.py
  GROMMUNIO_TARGET_DIR=/opt/grommunio python3 generate_grommunio_setup.py
  GROMMUNIO_ENV_FILE=/root/grommunio-generator.env python3 generate_grommunio_setup.py

Environment variables (or .env entries):
  GROMMUNIO_TARGET_DIR=/opt/grommunio
  GROMMUNIO_SOURCE_LOCK_FILE=./gromox-container.lock.json
  GROMMUNIO_SOURCE_CACHE_DIR=~/.cache/groupware-grommunio/gromox-container
  GROMMUNIO_EDGE_NETWORK=edge
  GROMMUNIO_EDGE_ALIAS=grommunio-web
  GROMMUNIO_MAILHUB_NETWORK=mailhub
  GROMMUNIO_MAILHUB_ALIAS=grommunio-internal
  GROMMUNIO_INTERNAL_HOSTNAME=gromox-int
  GROMMUNIO_WEB_BIND=
  GROMMUNIO_SMTPS_BIND=
  GROMMUNIO_IMAPS_BIND=
  GROMMUNIO_ADMIN_BIND=
  GROMMUNIO_TZ=Europe/Zurich
  GROMMUNIO_FQDN=grommunio.example.com
  GROMMUNIO_DOMAIN=mail.example.com
  GROMMUNIO_DOMAIN_MAX_USERS=25
  GROMMUNIO_ORGANIZATION=mail.example.com
  GROMMUNIO_ADMIN_PASS=...
  GROMMUNIO_MAILBOX_EMAIL=grommunio-user@mail.example.com
  GROMMUNIO_MAILBOX_CN=grommunio-user
  GROMMUNIO_RELAYHOST=[mailu-smtp]:25
  GROMMUNIO_ENABLE_CHAT=false
  GROMMUNIO_DB_IMAGE=mariadb:10
  GROMMUNIO_DB_NAME=grommunio
  GROMMUNIO_DB_USER=grommunio
  GROMMUNIO_DB_PASSWORD=...
  GROMMUNIO_DB_ROOT_PASSWORD=...
  GROMMUNIO_SSL_INSTALL_TYPE=0
  GROMMUNIO_SSL_COUNTRY=XX
  GROMMUNIO_SSL_STATE=XX
  GROMMUNIO_SSL_LOCALITY=X
  GROMMUNIO_SSL_ORG=grommunio Appliance
  GROMMUNIO_SSL_OU=IT
  GROMMUNIO_SSL_EMAIL=admin@mail.example.com
  GROMMUNIO_SSL_DAYS=30
  GROMMUNIO_SSL_PASS=...
"""

from __future__ import annotations

import os
import secrets
import shutil
import shlex
import hashlib
import json
import subprocess
import tarfile
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from xml.sax.saxutils import escape as xml_escape

try:
    from jinja2 import Environment, FileSystemLoader, StrictUndefined
except ModuleNotFoundError as exc:
    raise SystemExit(
        "generate_grommunio_setup.py requires Jinja2. Install python3-jinja2 or `pip install jinja2`."
    ) from exc


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


def write_executable_file(path: Path, content: str) -> None:
    write_file(path, content)
    path.chmod(0o755)


def xml_text(value: str) -> str:
    return xml_escape(value, {"'": "&apos;", '"': "&quot;"})


def shell_quote(value: str) -> str:
    return shlex.quote(value)


def build_template_environment(template_root: Path) -> Environment:
    environment = Environment(
        loader=FileSystemLoader(str(template_root)),
        autoescape=False,
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
        undefined=StrictUndefined,
    )
    environment.filters["shell_quote"] = shell_quote
    return environment


def render_template(environment: Environment, template_name: str, context: dict[str, object]) -> str:
    return environment.get_template(template_name).render(**context)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def default_archive_cache_dir() -> Path:
    xdg_cache_home = os.environ.get("XDG_CACHE_HOME")
    if xdg_cache_home:
        return Path(xdg_cache_home).expanduser() / "groupware-grommunio" / "gromox-container"
    return Path.home() / ".cache" / "groupware-grommunio" / "gromox-container"


def load_archive_lock(lock_path: Path, cache_dir: Path) -> tuple[dict[str, str], Path]:
    if not lock_path.exists():
        raise SystemExit(f"missing gromox-container lockfile at {lock_path}")

    try:
        metadata = json.loads(lock_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid JSON in {lock_path}: {exc}") from exc

    required = {"repo", "commit", "archive_url", "sha256"}
    missing = sorted(required - metadata.keys())
    if missing:
        raise SystemExit(f"missing keys in {lock_path}: {', '.join(missing)}")

    repo_name = metadata["repo"].split("/")[-1]
    archive_name = f"{repo_name}-{metadata['commit']}.tar.gz"
    archive_path = cache_dir / archive_name

    return metadata, archive_path


def download_archive(url: str, destination: Path) -> None:
    ensure_dir(destination.parent)
    request = Request(url, headers={"User-Agent": "generate_grommunio_setup.py"})
    tmp_path = destination.with_suffix(destination.suffix + ".tmp")
    try:
        with urlopen(request, timeout=60) as response, tmp_path.open("wb") as handle:
            shutil.copyfileobj(response, handle)
    except (HTTPError, URLError, OSError) as exc:
        tmp_path.unlink(missing_ok=True)
        raise SystemExit(f"failed to fetch {url}: {exc}") from exc

    tmp_path.replace(destination)


def ensure_cached_archive(lock_path: Path, cache_dir: Path) -> tuple[dict[str, str], Path]:
    metadata, archive_path = load_archive_lock(lock_path, cache_dir)
    fetched = False
    if not archive_path.exists():
        print(f"Fetching pinned archive {metadata['archive_url']} -> {archive_path}")
        download_archive(metadata["archive_url"], archive_path)
        fetched = True

    actual_sha = sha256_file(archive_path)
    if actual_sha != metadata["sha256"]:
        if fetched:
            archive_path.unlink(missing_ok=True)
        raise SystemExit(
            f"sha256 mismatch for {archive_path}: expected {metadata['sha256']}, got {actual_sha}"
        )

    return metadata, archive_path


def extract_gromox_core(archive_path: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    ensure_dir(destination)

    extracted_any = False
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive.getmembers():
            parts = Path(member.name).parts
            if len(parts) < 2 or parts[1] != "gromox-core":
                continue

            extracted_any = True
            if len(parts) == 2:
                continue

            relative = Path(*parts[2:])
            if any(part in ("", ".", "..") for part in relative.parts):
                raise SystemExit(f"unsafe archive path in {archive_path}: {member.name}")
            target = destination / relative

            if member.isdir():
                ensure_dir(target)
                target.chmod(member.mode & 0o777)
                continue

            if not member.isfile():
                raise SystemExit(f"unsupported archive member in {archive_path}: {member.name}")

            extracted = archive.extractfile(member)
            if extracted is None:
                raise SystemExit(f"failed to read {member.name} from {archive_path}")
            ensure_dir(target.parent)
            with extracted, target.open("wb") as handle:
                shutil.copyfileobj(extracted, handle)
            target.chmod(member.mode & 0o777)

    if not extracted_any:
        raise SystemExit(f"archive {archive_path} does not contain gromox-core")


def apply_unified_patch(patch_path: Path, destination_dir: Path) -> None:
    if not patch_path.exists():
        raise SystemExit(f"missing gromox-core patch at {patch_path}")

    try:
        result = subprocess.run(
            ["patch", "--batch", "--forward", "-p1", "-i", str(patch_path)],
            cwd=destination_dir,
            text=True,
            capture_output=True,
            check=True,
        )
    except FileNotFoundError as exc:
        raise SystemExit("required `patch` command not found in PATH") from exc
    except subprocess.CalledProcessError as exc:
        details = (exc.stdout + exc.stderr).strip()
        raise SystemExit(f"failed to apply {patch_path} in {destination_dir}:\n{details}") from exc

    if result.stdout.strip():
        print(result.stdout.strip())
    if result.stderr.strip():
        print(result.stderr.strip())


def main() -> int:
    script_dir = Path(__file__).resolve().parent
    lock_path = Path(
        os.environ.get("GROMMUNIO_SOURCE_LOCK_FILE", script_dir / "gromox-container.lock.json")
    ).expanduser()
    cache_dir = Path(
        os.environ.get("GROMMUNIO_SOURCE_CACHE_DIR", default_archive_cache_dir())
    ).expanduser()
    archive_metadata, archive_path = ensure_cached_archive(lock_path, cache_dir)

    env_file = Path(os.environ.get("GROMMUNIO_ENV_FILE", ".env"))
    file_values = parse_dotenv(env_file)

    target_dir = Path(getenv("GROMMUNIO_TARGET_DIR", "/opt/grommunio", file_values)).expanduser()
    edge_network = getenv("GROMMUNIO_EDGE_NETWORK", "", file_values)
    edge_alias = getenv("GROMMUNIO_EDGE_ALIAS", "", file_values)
    mailhub_network = getenv("GROMMUNIO_MAILHUB_NETWORK", "mailhub", file_values)
    mailhub_alias = getenv("GROMMUNIO_MAILHUB_ALIAS", "grommunio-internal", file_values)
    internal_hostname = getenv("GROMMUNIO_INTERNAL_HOSTNAME", "gromox-int", file_values)
    web_bind = getenv("GROMMUNIO_WEB_BIND", "", file_values)
    smtps_bind = getenv("GROMMUNIO_SMTPS_BIND", "", file_values)
    imaps_bind = getenv("GROMMUNIO_IMAPS_BIND", "", file_values)
    admin_bind = getenv("GROMMUNIO_ADMIN_BIND", "", file_values)

    timezone = getenv("GROMMUNIO_TZ", "Europe/Zurich", file_values)
    fqdn = getenv("GROMMUNIO_FQDN", "grommunio.example.com", file_values)
    domain = getenv("GROMMUNIO_DOMAIN", "mail.example.com", file_values)
    domain_max_users = getenv("GROMMUNIO_DOMAIN_MAX_USERS", "25", file_values)
    organization = getenv("GROMMUNIO_ORGANIZATION", "grommunio", file_values)
    admin_pass = getenv("GROMMUNIO_ADMIN_PASS", "", file_values) or random_password()
    mailbox_email = getenv("GROMMUNIO_MAILBOX_EMAIL", f"grommunio-user@{domain}", file_values)
    mailbox_cn = getenv("GROMMUNIO_MAILBOX_CN", mailbox_email.split("@", 1)[0], file_values)
    relayhost = getenv("GROMMUNIO_RELAYHOST", "[mailu-smtp]:25", file_values)
    enable_chat = getenv("GROMMUNIO_ENABLE_CHAT", "false", file_values).lower() == "true"
    mailbox_domain = mailbox_email.split("@", 1)[1]
    db_image = getenv("GROMMUNIO_DB_IMAGE", "mariadb:10", file_values)
    db_name = getenv("GROMMUNIO_DB_NAME", "grommunio", file_values)
    db_user = getenv("GROMMUNIO_DB_USER", "grommunio", file_values)
    db_password = getenv("GROMMUNIO_DB_PASSWORD", "", file_values) or random_password()
    db_root_password = getenv("GROMMUNIO_DB_ROOT_PASSWORD", "", file_values) or random_password()

    chat_db_image = getenv("GROMMUNIO_CHAT_DB_IMAGE", db_image, file_values)
    chat_db_name = getenv("GROMMUNIO_CHAT_DB_NAME", "grochat", file_values)
    chat_db_user = getenv("GROMMUNIO_CHAT_DB_USER", "grochat", file_values)
    chat_db_password = getenv("GROMMUNIO_CHAT_DB_PASSWORD", "", file_values) or random_password()

    ssl_install_type = getenv("GROMMUNIO_SSL_INSTALL_TYPE", "0", file_values)
    ssl_country = getenv("GROMMUNIO_SSL_COUNTRY", "XX", file_values)
    ssl_state = getenv("GROMMUNIO_SSL_STATE", "XX", file_values)
    ssl_locality = getenv("GROMMUNIO_SSL_LOCALITY", "X", file_values)
    ssl_org = getenv("GROMMUNIO_SSL_ORG", "grommunio Appliance", file_values)
    ssl_ou = getenv("GROMMUNIO_SSL_OU", "IT", file_values)
    ssl_email = getenv("GROMMUNIO_SSL_EMAIL", f"admin@{domain}", file_values)
    ssl_days = getenv("GROMMUNIO_SSL_DAYS", "30", file_values)
    ssl_pass = getenv("GROMMUNIO_SSL_PASS", "", file_values) or random_password()

    if edge_alias and not edge_network:
        raise SystemExit("GROMMUNIO_EDGE_ALIAS requires GROMMUNIO_EDGE_NETWORK to be set")

    template_env = build_template_environment(script_dir / "templates")
    gromox_patch_path = script_dir / "patches" / "gromox-core.patch"
    data_root = target_dir / "data" / "grommunio"
    postfix_dir = target_dir / "postfix"
    postfix_transport_seed = postfix_dir / "transport.seed"
    variables_dir = data_root / "variables"
    for path in [
        postfix_dir,
        data_root / "db" / "mysql",
        data_root / "db" / "chat",
        data_root / "certs",
        data_root / "gromox_config",
        data_root / "gromox" / "domain",
        data_root / "gromox" / "queue" / "cache",
        data_root / "gromox" / "queue" / "mess",
        data_root / "gromox" / "queue" / "save",
        data_root / "gromox" / "queue" / "timer",
        data_root / "gromox" / "user",
        data_root / "gromox_services",
        data_root / "admin_api",
        data_root / "antispam",
        data_root / "dav",
        data_root / "web" / "session",
        data_root / "web" / "sqlite-index",
        data_root / "web" / "tmp",
        data_root / "letsencrypt",
        data_root / "setup",
        variables_dir,
    ]:
        ensure_dir(path)

    copied_core_dir = target_dir / "gromox-core"
    with tempfile.TemporaryDirectory(prefix="gromox-container-") as tmp_dir_name:
        extracted_core_dir = Path(tmp_dir_name) / "gromox-core"
        extract_gromox_core(archive_path, extracted_core_dir)
        if copied_core_dir.exists():
            shutil.rmtree(copied_core_dir)
        shutil.copytree(extracted_core_dir, copied_core_dir)
    apply_unified_patch(gromox_patch_path, copied_core_dir)
    if not postfix_transport_seed.exists():
        write_file(postfix_transport_seed, "")

    chat_admin_pass = random_password(12)
    files_admin_pass = random_password(12)
    published_ports = [port for port in [web_bind, smtps_bind, imaps_bind, admin_bind] if port]
    exposed_ports = [
        "24",
        "8080",
        "8443",
        "9443",
        "2525",
        "2465",
        "2587",
        "2143",
        "2993",
        "2110",
        "2995",
    ]
    exposed_port_lines = "\n".join(f'- "{port}"' for port in exposed_ports)
    published_port_lines = "\n".join(f'- "{port}"' for port in published_ports)
    template_context: dict[str, object] = {
        "admin_pass": admin_pass,
        "chat_admin_pass": chat_admin_pass,
        "chat_db_image": chat_db_image,
        "chat_db_name": chat_db_name,
        "chat_db_password": chat_db_password,
        "chat_db_user": chat_db_user,
        "db_image": db_image,
        "db_name": db_name,
        "db_password": db_password,
        "db_root_password": db_root_password,
        "db_user": db_user,
        "domain": domain,
        "domain_max_users": domain_max_users,
        "edge_alias": edge_alias,
        "edge_network": edge_network,
        "enable_chat": enable_chat,
        "exposed_port_lines": exposed_port_lines,
        "files_admin_pass": files_admin_pass,
        "fqdn": fqdn,
        "internal_hostname": internal_hostname,
        "mailbox_email": mailbox_email,
        "mailhub_alias": mailhub_alias,
        "mailhub_network": mailhub_network,
        "organization": organization,
        "published_ports": published_ports,
        "published_port_lines": published_port_lines,
        "relayhost": relayhost,
        "ssl_country": ssl_country,
        "ssl_days": ssl_days,
        "ssl_email": ssl_email,
        "ssl_install_type": ssl_install_type,
        "ssl_locality": ssl_locality,
        "ssl_org": ssl_org,
        "ssl_ou": ssl_ou,
        "ssl_pass": ssl_pass,
        "ssl_state": ssl_state,
        "timezone": timezone,
    }

    files = {
        variables_dir / "var.env": render_template(
            template_env, "grommunio/var.env.j2", template_context
        ),
        target_dir / "docker-compose.yml": render_template(
            template_env, "grommunio/docker-compose.yml.j2", template_context
        ),
        target_dir / "README.txt": render_template(template_env, "README.txt.j2", template_context),
        target_dir / "provision-grommunio.sh": render_template(
            template_env, "grommunio/provision-grommunio.sh.j2", template_context
        ),
        target_dir / "verify-grommunio.sh": render_template(
            template_env, "grommunio/verify-grommunio.sh.j2", template_context
        ),
    }
    executable_files = {
        target_dir / "provision-grommunio.sh",
        target_dir / "verify-grommunio.sh",
    }
    for path, content in files.items():
        if path in executable_files:
            write_executable_file(path, content)
        else:
            write_file(path, content)

    shell_target_dir = shlex.quote(str(target_dir))
    print(f"Created {target_dir}")
    print(f"Used source lock:   {lock_path}")
    print(f"Archive cache dir:  {archive_path.parent}")
    print(f"Used pinned archive: {archive_path}")
    print(f"Archive repo:        {archive_metadata['repo']}")
    print(f"Archive commit:      {archive_metadata['commit']}")
    print(f"Extracted gromox-core -> {copied_core_dir}")
    print(f"Applied patch:       {gromox_patch_path}")
    for path in files:
        print(f"Created {path}")
    print()
    print("Generated values:")
    print(f"  FQDN:              {fqdn}")
    print(f"  Mail domain:       {domain}")
    print(f"  Domain max users:  {domain_max_users}")
    print(f"  Admin password:    {admin_pass}")
    print(f"  Mailbox email:     {mailbox_email}")
    print(f"  Mailbox CN:        {mailbox_cn}")
    print(f"  Edge network:      {edge_network or '(not attached)'}")
    print(f"  Edge alias:        {edge_alias or '(none)'}")
    print(f"  Mailhub network:   {mailhub_network}")
    print(f"  Mailhub alias:     {mailhub_alias}")
    print(f"  Internal host:     {internal_hostname}")
    print(f"  Relayhost:         {relayhost}")
    print(f"  Web bind:          {web_bind or '(not published)'}")
    print(f"  SMTPS bind:        {smtps_bind or '(not published)'}")
    print(f"  IMAPS bind:        {imaps_bind or '(not published)'}")
    print(f"  Chat enabled:      {'yes' if enable_chat else 'no'}")
    print(f"  DB image:          {db_image}")
    print(f"  DB name:           {db_name}")
    print(f"  DB user:           {db_user}")
    print(f"  DB password:       {db_password}")
    print(f"  DB root password:  {db_root_password}")
    print()
    print("Build and start the stack:")
    print(f"  cd {shell_target_dir}")
    print("  docker compose build")
    print("  docker compose up -d")
    print()
    print("Provision the server, domain, and mailbox locally from the Docker host:")
    print(f"  cd {shell_target_dir}")
    print("  GROMMUNIO_MAILBOX_PASSWORD='...' ./provision-grommunio.sh")
    print()
    print("Run the local verification helper:")
    print(f"  cd {shell_target_dir}")
    print("  GROMMUNIO_MAILBOX_PASSWORD='...' ./verify-grommunio.sh")
    print()
    print("Inspect service status after startup:")
    print(f"  cd {shell_target_dir}")
    print("  docker compose exec gromox-core supervisorctl status")
    print("  docker compose exec gromox-core grommunio-admin domain -h")
    print("  docker compose exec gromox-core grommunio-admin user -h")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
