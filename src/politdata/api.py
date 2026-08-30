
from math import ceil
import time

import requests


DEFAULT_BASE_URL = "https://politdata.nazk.gov.ua/api/v2"


def fetch_all_parties(
    base_url=DEFAULT_BASE_URL,
    page_size=100,
    timeout=30,
):
    """
    Download the current /parties index from PolitData.

    Important:
    This function performs fresh discovery every time it runs.
    It does not rely on a previously saved list of party IDs.
    """

    endpoint = f"{base_url}/parties"

    session = requests.Session()

    def fetch_page(page):
        payload = {
            "filters": {},
            "order": {},
            "pager": {
                "page": page,
                "size": page_size,
            },
        }

        response = session.post(
            endpoint,
            json=payload,
            timeout=timeout,
        )

        response.raise_for_status()

        return response.json()["results"]

    first_page = fetch_page(1)

    total_count = first_page["count"]
    total_pages = ceil(total_count / page_size)

    records = list(first_page["list"])

    for page in range(2, total_pages + 1):
        page_results = fetch_page(page)
        records.extend(page_results["list"])

    if len(records) != total_count:
        raise ValueError(
            f"PolitData reported {total_count} records, "
            f"but {len(records)} were downloaded."
        )

    ids = [record["id"] for record in records]

    if len(ids) != len(set(ids)):
        raise ValueError(
            "Duplicate organization IDs returned by /parties."
        )

    return records


def fetch_party_account(
    organization_id,
    base_url=DEFAULT_BASE_URL,
    timeout=30,
    max_retries=4,
):
    """
    Download the full PolitData account for one party
    or regional/local party organization.
    """

    url = f"{base_url}/party/{organization_id}"

    session = requests.Session()

    last_error = None

    for attempt in range(1, max_retries + 1):

        try:
            response = session.get(
                url,
                timeout=timeout,
            )

            response.raise_for_status()

            data = response.json()

            if "results" not in data:
                raise ValueError(
                    f"No 'results' field for {organization_id}"
                )

            returned_id = data["results"].get("id")

            if returned_id != organization_id:
                raise ValueError(
                    f"Requested {organization_id}, "
                    f"but API returned {returned_id}"
                )

            return data

        except Exception as exc:
            last_error = exc

            if attempt < max_retries:
                time.sleep(attempt * 2)

    raise last_error
