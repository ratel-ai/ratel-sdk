"""A run in mode `simulated` answers from the fake refund, and the real refund never runs."""

from __future__ import annotations

import json
import unittest
import urllib.request
from typing import Any
from unittest import mock

import agent
import tools
from ratel_ai.serve import start_server


def post(port: int, body: dict[str, Any]) -> Any:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/ratel/run",
        data=json.dumps(body).encode(),
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as res:
        return json.loads(res.read())


class AgentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.server = start_server(agent.run, port=0, host="127.0.0.1")

    def tearDown(self) -> None:
        self.server.close()

    def run_message(self, message: str, refund_mode: str = "simulated") -> Any:
        return post(
            self.server.port,
            {
                "v": 1,
                "runId": "test",
                "input": {"message": message},
                "params": {"tone": "formal"},
                "tools": {
                    "lookup_order": {"mode": "live"},
                    "refund_order": {"mode": refund_mode},
                },
                "fixtures": [],
            },
        )

    def test_a_simulated_refund_never_runs_the_real_refund(self) -> None:
        with mock.patch.object(tools, "print", create=True) as printed:
            body = self.run_message("Order A100 never worked. Refund it please.")
        printed.assert_not_called()
        self.assertEqual(body["output"]["refund"]["real"], False)
        self.assertEqual(
            [(c["name"], c["mode"]) for c in body["toolCalls"]],
            [("lookup_order", "live"), ("refund_order", "simulated")],
        )

    def test_an_old_order_gets_no_refund(self) -> None:
        body = self.run_message(
            "My vase from order A200 arrived damaged. Refund it please."
        )
        self.assertIn("window", body["output"]["reply"])
        self.assertEqual([c["name"] for c in body["toolCalls"]], ["lookup_order"])


if __name__ == "__main__":
    unittest.main()
