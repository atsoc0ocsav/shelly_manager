# Third-party notices

Shelly Manager is licensed under the MIT licence (see [LICENSE](LICENSE)). It does not
include third-party source code in this repository. The components below are downloaded
when the app is built and are distributed under their own licences.

## Python packages (runtime)

Versions as pinned in `shelly_manager/uv.lock` on 2026-10-16. Licences as declared in each
package's metadata.

| Package | Version | Licence | Project |
|---|---|---|---|
| annotated-doc | 0.0.5 | MIT | https://github.com/fastapi/annotated-doc |
| annotated-types | 0.8.0 | MIT | https://github.com/annotated-types/annotated-types |
| anyio | 4.15.1 | MIT | https://github.com/agronholm/anyio |
| bcrypt | 5.0.0 | Apache-2.0 | https://github.com/pyca/bcrypt |
| certifi | 2026.7.22 | MPL-2.0 | https://github.com/certifi/python-certifi |
| click | 8.5.0 | BSD-3-Clause | https://github.com/pallets/click |
| fastapi | 0.143.0 | MIT | https://github.com/fastapi/fastapi |
| h11 | 0.16.0 | MIT | https://github.com/python-hyper/h11 |
| httpcore | 1.0.9 | BSD-3-Clause | https://github.com/encode/httpcore |
| httpx | 0.28.1 | BSD-3-Clause | https://github.com/encode/httpx |
| idna | 3.20 | BSD-3-Clause | https://github.com/kjd/idna |
| itsdangerous | 2.2.0 | BSD-3-Clause | https://github.com/pallets/itsdangerous |
| jinja2 | 3.1.6 | BSD-3-Clause | https://github.com/pallets/jinja |
| markupsafe | 3.0.4 | BSD-3-Clause | https://github.com/pallets/markupsafe |
| opentelemetry-api | 1.45.1 | Apache-2.0 | https://github.com/open-telemetry/opentelemetry-python |
| pydantic | 2.14.1 | MIT | https://github.com/pydantic/pydantic |
| pydantic-core | 2.50.1 | MIT | https://github.com/pydantic/pydantic |
| python-multipart | 0.0.32 | Apache-2.0 | https://github.com/Kludex/python-multipart |
| starlette | 1.8.0 | BSD-3-Clause | https://github.com/Kludex/starlette |
| typing-extensions | 4.16.0 | PSF-2.0 | https://github.com/python/typing_extensions |
| typing-inspection | 0.4.4 | MIT | https://github.com/pydantic/typing-inspection |
| uvicorn | 0.54.0 | BSD-3-Clause | https://github.com/Kludex/uvicorn |

`certifi` is MPL-2.0, a file-level copyleft licence. It is used unmodified; its source is
available from the project link above.

## Container image

| Component | Licence | Project |
|---|---|---|
| Home Assistant base-python image (Alpine Linux, Python, S6 overlay, Bashio) | Apache-2.0 for the image definition; bundled packages under their own licences | https://github.com/home-assistant/docker-base |
| git, openssh-client (Alpine packages) | GPL-2.0 / BSD | https://alpinelinux.org |
| uv (used at build time only, not shipped in the image) | MIT or Apache-2.0 | https://github.com/astral-sh/uv |

## Trademarks

Shelly is a trademark of Shelly Group. This project is not affiliated with, endorsed or
sponsored by Shelly Group or by the Home Assistant project / Open Home Foundation. The
names are used only to describe compatibility.
