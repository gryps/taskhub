import json

from taskhub_v2.workflows.evidence import delivery_evidence


def test_delivery_evidence_distinguishes_coding_from_verification_commands():
    payload = json.loads(delivery_evidence({
        "implementation": {
            "evidence": "Coding model changed one file without installing dependencies.",
            "tests": [{"command": ["npm", "run", "bootstrap"], "exit_code": 0}],
        },
        "acceptance": {
            "evidence": [{
                "id": "project-acceptance",
                "tests": [{"command": ["npm", "run", "test"], "exit_code": 0}],
            }],
        },
    }))

    provenance = payload["evidence_provenance"]
    assert "coding model" in provenance["implementation.evidence"]
    assert "not actions performed by the coding model" in provenance["implementation.tests"]
    assert "independent verification" in provenance["acceptance.evidence"]
    assert payload["implementation"]["tests"][0]["command"][-1] == "bootstrap"
