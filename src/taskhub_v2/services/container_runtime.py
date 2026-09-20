def node_runtime_configuration(
    role: str,
    data_volume_name: str,
    model_accounts_volume_subpath: str,
    openai_proxy_url: str,
) -> tuple[list[str], dict]:
    coding = role == "execution"
    environment = [f"TASKHUB_NODE_CODING_ENABLED={'true' if coding else 'false'}"]
    host_config = {"SecurityOpt": ["seccomp=unconfined"]} if coding else {}
    if not coding or not data_volume_name or not model_accounts_volume_subpath:
        return environment, host_config
    account_root = "/var/lib/taskhub-node/codex/accounts"
    environment.extend(
        [
            f"TASKHUB_MODEL_ACCOUNT_ROOT={account_root}",
            f"TASKHUB_MODEL_CARDS_FILE={account_root}/node-models.json",
            f"TASKHUB_OPENAI_PROXY_URL={openai_proxy_url}",
        ]
    )
    host_config["Mounts"] = [
        {
            "Type": "volume",
            "Source": data_volume_name,
            "Target": account_root,
            "ReadOnly": False,
            "VolumeOptions": {"Subpath": model_accounts_volume_subpath},
        }
    ]
    return environment, host_config
