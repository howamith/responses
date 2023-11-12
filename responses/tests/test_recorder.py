from pathlib import Path
from types import ModuleType
from typing import Any
from typing import Dict
from typing import List
from typing import Tuple
from typing import cast

import pytest
import requests
import tomli_w
import yaml
from pytest_httpserver import HTTPServer

import responses
from responses import _recorder
from responses._recorder import _dump

try:
    import tomli as _toml
except ImportError:
    # python 3.11
    import tomllib as _toml  # type: ignore[no-redef]


def get_data(host: str, port: int) -> Dict[str, Any]:
    data = {
        "responses": [
            {
                "response": {
                    "method": "GET",
                    "url": f"http://{host}:{port}/404",
                    "body": "404 Not Found",
                    "status": 404,
                    "content_type": "text/plain",
                    "auto_calculate_content_length": False,
                }
            },
            {
                "response": {
                    "method": "GET",
                    "url": f"http://{host}:{port}/status/wrong",
                    "body": "Invalid status code",
                    "status": 400,
                    "content_type": "text/plain",
                    "auto_calculate_content_length": False,
                }
            },
            {
                "response": {
                    "method": "GET",
                    "url": f"http://{host}:{port}/500",
                    "body": "500 Internal Server Error",
                    "status": 500,
                    "content_type": "text/plain",
                    "auto_calculate_content_length": False,
                }
            },
            {
                "response": {
                    "method": "PUT",
                    "url": f"http://{host}:{port}/202",
                    "body": "OK",
                    "status": 202,
                    "content_type": "text/plain",
                    "auto_calculate_content_length": False,
                }
            },
        ]
    }
    return data


class TestRecord:
    def setup_method(self) -> None:
        self.out_file = Path("response_record")
        if self.out_file.exists():
            self.out_file.unlink()  # pragma: no cover

        assert not self.out_file.exists()

    def test_recorder(self, httpserver: HTTPServer) -> None:
        url202, url400, url404, url500 = self.prepare_server(httpserver)

        def another() -> None:
            requests.get(url500)
            requests.put(url202)

        @_recorder.record(file_path=self.out_file)
        def run() -> None:
            requests.get(url404)
            requests.get(url400)
            another()

        run()

        with open(self.out_file) as file:
            data = yaml.safe_load(file)

        assert data == get_data(httpserver.host, httpserver.port)

    def test_recorder_toml(self, httpserver: HTTPServer) -> None:
        custom_recorder = _recorder.Recorder()

        def dump_to_file(file_path, registered):
            with open(file_path, "wb") as file:
                _dump(registered, file, tomli_w.dump)

        custom_recorder.dump_to_file = dump_to_file

        url202, url400, url404, url500 = self.prepare_server(httpserver)

        def another() -> None:
            requests.get(url500)
            requests.put(url202)

        @custom_recorder.record(file_path=self.out_file)
        def run() -> None:
            requests.get(url404)
            requests.get(url400)
            another()

        run()

        with open(self.out_file, "rb") as file:
            data = _toml.load(file)

        assert data == get_data(httpserver.host, httpserver.port)

    def prepare_server(self, httpserver: HTTPServer) -> Tuple[str, str, str, str]:
        httpserver.expect_request("/500").respond_with_data(
            "500 Internal Server Error", status=500, content_type="text/plain"
        )
        httpserver.expect_request("/202").respond_with_data(
            "OK", status=202, content_type="text/plain"
        )
        httpserver.expect_request("/404").respond_with_data(
            "404 Not Found", status=404, content_type="text/plain"
        )
        httpserver.expect_request("/status/wrong").respond_with_data(
            "Invalid status code", status=400, content_type="text/plain"
        )
        url500 = httpserver.url_for("/500")
        url202 = httpserver.url_for("/202")
        url404 = httpserver.url_for("/404")
        url400 = httpserver.url_for("/status/wrong")
        return url202, url400, url404, url500


class TestReplay:
    def setup_method(self) -> None:
        self.out_file = Path("response_record")

    def teardown_method(self) -> None:
        if self.out_file.exists():
            self.out_file.unlink()

        assert not self.out_file.exists()

    @pytest.mark.parametrize("parser", (yaml, tomli_w))
    def test_add_from_file(self, parser: ModuleType) -> None:
        if parser == yaml:
            with open(self.out_file, "w") as file:
                parser.dump(get_data("example.com", "8080"), file)
        else:
            with open(self.out_file, "wb") as file:
                parser.dump(get_data("example.com", "8080"), file)

        @responses.activate
        def run() -> None:
            responses.patch("http://httpbin.org")
            if parser == tomli_w:

                def _parse_response_file(file_path):
                    with open(file_path, "rb") as file:
                        data = _toml.load(file)
                    return data

                setattr(responses.mock, "_parse_response_file", _parse_response_file)

            responses._add_from_file(file_path=self.out_file)
            responses.post("http://httpbin.org/form")

            registered = cast(List[responses.Response], responses.registered())

            assert registered[0].url == "http://httpbin.org/"
            assert registered[1].url == "http://example.com:8080/404"
            assert registered[2].url == "http://example.com:8080/status/wrong"
            assert registered[3].url == "http://example.com:8080/500"
            assert registered[4].url == "http://example.com:8080/202"
            assert registered[5].url == "http://httpbin.org/form"

            assert registered[0].method == "PATCH"
            assert registered[2].method == "GET"
            assert registered[4].method == "PUT"
            assert registered[5].method == "POST"

            assert registered[2].status == 400
            assert registered[3].status == 500

            assert registered[3].body == "500 Internal Server Error"

            assert registered[3].content_type == "text/plain"

        run()
