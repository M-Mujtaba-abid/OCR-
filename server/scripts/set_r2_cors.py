"""Put the CORS policy the browser upload needs onto the R2 bucket.

Invoices go browser -> R2 directly, because a serverless request body is capped
at 4.5 MB and a scanned invoice routinely exceeds it. That makes the PUT a
cross-origin request, and R2 buckets ship with NO CORS policy at all — so
without this the browser refuses the upload before sending a single byte, and
the network tab shows a CORS error of 0 bytes rather than anything server-side.

Two things about that PUT force a preflight, and both must be allowed here:

  * `Content-Type` is safelisted only for form and plain-text values.
    "application/pdf" is not one of them.
  * `Content-Disposition` is not safelisted at all.

Both are signed INTO the presigned URL by `storage.generate_upload_url`, so
they cannot simply be dropped: a PUT whose headers do not match the signature is
refused by R2 itself.

    .\\.venv\\Scripts\\python.exe scripts\\set_r2_cors.py --show
    .\\.venv\\Scripts\\python.exe scripts\\set_r2_cors.py --origin https://invoices.example.com

PERMISSIONS. This needs an R2 token with **Admin Read & Write**. The token the
app runs on is object-scoped — correct for the app, and deliberately unable to
change bucket settings — so it will fail here with AccessDenied. Either use an
admin token for this one call, or paste the policy `--show` prints into the
Cloudflare dashboard under R2 -> your bucket -> Settings -> CORS Policy.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Allows `python scripts/set_r2_cors.py` to import `app.*` without installing
# the package or setting PYTHONPATH by hand.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from botocore.exceptions import ClientError  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.storage import get_r2_client  # noqa: E402


def rules(origins: list[str]) -> list[dict[str, Any]]:
    """The narrowest policy that lets the upload through.

    PUT only. A presigned GET is opened as a navigation rather than fetched by
    script, so it never asks for a preflight and needs nothing here — and every
    method added is one more thing a stolen presigned URL could do from a page
    the user did not write.
    """
    return [
        {
            "AllowedOrigins": origins,
            "AllowedMethods": ["PUT"],
            # Exactly what `generate_upload_url` signs, and therefore exactly
            # what the browser must be allowed to send.
            "AllowedHeaders": ["content-type", "content-disposition"],
            "ExposeHeaders": ["ETag"],
            # One preflight per origin per hour instead of one per file. A
            # twenty-file upload otherwise pays for twenty round trips before
            # any bytes move.
            "MaxAgeSeconds": 3600,
        }
    ]


def check(origins: list[str]) -> int:
    """Ask the bucket what it would allow, the way a browser does.

    A real preflight against a real presigned URL, because that is the only
    thing that answers the question. `get_bucket_cors` needs admin rights the
    app's token does not have, and the Cloudflare dashboard showing a saved
    policy is not proof that the S3 endpoint serves it.

    A bucket with NO policy answers 403 to every OPTIONS, including one that
    requests no headers at all — so a 403 here means "no rules", not "wrong
    rules", and the two need very different fixes.
    """
    import anyio
    import urllib.error
    import urllib.request

    from app.core import storage

    async def probe() -> int:
        url = await storage.generate_upload_url(
            "cors-probe/probe.pdf", mime_type="application/pdf", original_name="probe.pdf"
        )
        print(f"Bucket {settings.R2_BUCKET_NAME} via {url.split('/')[2]}\n")

        failures = 0
        for origin in origins:
            request = urllib.request.Request(url, method="OPTIONS")
            request.add_header("Origin", origin)
            request.add_header("Access-Control-Request-Method", "PUT")
            request.add_header(
                "Access-Control-Request-Headers", "content-type,content-disposition"
            )
            try:
                response = urllib.request.urlopen(request, timeout=20)
                status, headers = response.status, dict(response.headers)
            except urllib.error.HTTPError as exc:
                status, headers = exc.code, dict(exc.headers)
            except OSError as exc:  # DNS, TLS, connection refused
                print(f"  {origin}: could not be reached — {exc}")
                failures += 1
                continue

            allowed = headers.get("Access-Control-Allow-Origin")
            if status == 200 and allowed:
                print(f"  {origin}: OK (allow-origin: {allowed})")
            else:
                print(f"  {origin}: BLOCKED ({status}, no allow-origin header)")
                failures += 1

        if failures:
            print(
                "\nThe browser upload will fail for the origins marked BLOCKED.\n"
                "Set the policy: --show, then paste it into\n"
                "  Cloudflare -> R2 -> your bucket -> Settings -> CORS Policy",
                file=sys.stderr,
            )
            return 1

        print("\nEvery origin passes preflight. The browser upload will work.")
        return 0

    return anyio.run(probe)


def _admin_client(access_key: str, secret_key: str) -> Any:
    """A one-off client on admin credentials, built like `storage.get_r2_client`.

    The same three R2-specific settings: `region_name="auto"` because SigV4
    needs a region in the signature, s3v4 because R2 rejects older ones, and
    checksums only when required because R2 refuses botocore's default CRC32 on
    every request.
    """
    import boto3
    from botocore.config import Config as BotoConfig

    return boto3.client(
        "s3",
        endpoint_url=f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="auto",
        config=BotoConfig(
            signature_version="s3v4",
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--origin",
        action="append",
        default=[],
        help=(
            "An origin to allow, e.g. https://invoices.example.com. Repeatable. "
            "Defaults to the API's own CORS_ORIGINS, which is right for local "
            "work and wrong for production — pass the deployed origin there."
        ),
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="Print the policy and exit. Paste it into the Cloudflare dashboard.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Send a real preflight and report whether each origin is allowed. "
            "Writes nothing. Run this after saving the policy."
        ),
    )
    parser.add_argument(
        "--access-key",
        help=(
            "An R2 access key with Admin Read & Write, used for this call only. "
            "Given here rather than in .env on purpose: the app's own token is "
            "object-scoped and should stay that way, and swapping it out and "
            "back is how a long-lived admin credential ends up in a running "
            "service by accident."
        ),
    )
    parser.add_argument("--secret-key", help="The secret paired with --access-key.")
    args = parser.parse_args()

    if bool(args.access_key) != bool(args.secret_key):
        print("--access-key and --secret-key go together.", file=sys.stderr)
        return 2

    origins = args.origin or list(settings.CORS_ORIGINS)
    if not origins:
        print("No origins. Pass --origin or set CORS_ORIGINS.", file=sys.stderr)
        return 2

    policy = rules(origins)

    if args.show:
        print(json.dumps(policy, indent=2))
        return 0

    if args.check:
        return check(origins)

    print(f"Bucket {settings.R2_BUCKET_NAME}: allowing PUT from {', '.join(origins)}")
    client = _admin_client(args.access_key, args.secret_key) if args.access_key else get_r2_client()
    try:
        client.put_bucket_cors(
            Bucket=settings.R2_BUCKET_NAME, CORSConfiguration={"CORSRules": policy}
        )
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code in ("AccessDenied", "Forbidden"):
            print(
                "\nAccessDenied — this R2 token cannot change bucket settings.\n"
                "That is expected for the object-scoped token the app uses.\n"
                "Either use a token with Admin Read & Write, or run with --show\n"
                "and paste the policy into the Cloudflare dashboard:\n"
                "  R2 -> your bucket -> Settings -> CORS Policy",
                file=sys.stderr,
            )
            return 1
        raise

    print("Done. Reload the upload page — no redeploy is needed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
