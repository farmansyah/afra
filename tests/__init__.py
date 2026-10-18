"""Offline tests. Run from the repository root:  python -m unittest discover -s tests -t .

Everything A.F.R.A writes goes to a temporary folder (RT_DATA_DIR, AFRA_SLIDES_DIR), never to data/ or ~/AFRA-Slides,
and no test talks to Google: the HTTP calls go to httpx.MockTransport."""
import atexit
import os
import shutil
import tempfile

TMP = tempfile.mkdtemp(prefix="afra-tests-")
os.environ["RT_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["AFRA_SLIDES_DIR"] = os.path.join(TMP, "AFRA-Slides")
os.environ["AFRA_GDRIVE_ROOT"] = os.path.join(TMP, "no-drive")  # not a folder: "Drive for desktop isn't installed"
atexit.register(shutil.rmtree, TMP, True)
