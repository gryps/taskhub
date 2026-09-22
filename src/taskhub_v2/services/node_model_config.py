import json
import shutil
from pathlib import Path

NODE_MODEL_CONFIG_FILENAME = "node-models.json"


def write_node_model_configuration(settings) -> Path | None:
    if not settings.data_volume_name or not settings.model_accounts_volume_subpath:
        return None
    account_root = Path(settings.model_account_root)
    account_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    runtime_root = account_root / "node-runtime"
    temporary_root = account_root / "node-runtime.tmp"
    if temporary_root.exists():
        shutil.rmtree(temporary_root)
    temporary_root.mkdir(mode=0o700)
    coder_cards = [
        card
        for card in settings.model_cards
        if card.get("enabled")
        and card.get("service_type") == "openai"
        and any(item.get("role") == "coder" for item in card.get("assignments", []))
    ]
    for card in coder_cards:
        if card.get("auth_mode") != "account":
            continue
        source = account_root / card["model_id"] / "auth.json"
        if source.is_file():
            target_account = temporary_root / card["model_id"]
            target_account.mkdir(mode=0o700)
            shutil.copyfile(source, target_account / "auth.json")
            (target_account / "auth.json").chmod(0o600)
    target = temporary_root / NODE_MODEL_CONFIG_FILENAME
    target.write_text(
        json.dumps(coder_cards, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    target.chmod(0o600)
    # Docker's volume-subpath mount is bound to this directory inode. Replacing
    # the directory leaves already-running coding nodes attached to an unlinked,
    # empty directory. Keep the root stable and replace only its children.
    runtime_root.mkdir(mode=0o700, exist_ok=True)
    desired = {child.name for child in temporary_root.iterdir()}
    for child in runtime_root.iterdir():
        if child.name in desired:
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    for child in temporary_root.iterdir():
        destination = runtime_root / child.name
        if child.is_dir():
            if destination.exists():
                shutil.rmtree(destination)
            child.replace(destination)
        else:
            child.replace(destination)
    temporary_root.rmdir()
    return runtime_root / NODE_MODEL_CONFIG_FILENAME
