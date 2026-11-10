r"""「📤 送出」:把一份逐字稿 md 複製進使用者選的送出資料夾(例如 `_WikiBox`,之後由
wiki-feeder 收進知識庫)。

(2026-11-10 使用者:「仿照 APP 的做法,有設定送入資料夾就出現送出的按鈕」;手機版規格在
`scribe2md-ios` 的 `docs/spec/08` §8.7、§8.8。)電腦版**直接複製檔案**,資料夾是本機或雲端
硬碟都可以——上傳是雲端硬碟自己做的,這支程式不連網。

交接契約(與手機版同一套,改了要兩個 repo 一起看):
(1) **只送 md 一個檔**:`.wav` 與「…重點.json」都不送。wiki-feeder 把同主檔名的檔當成一組,
    兩個都送等於同一場會議攝入兩次;重點與備註本來就寫在 md 裡。
(2) **先寫成「.」開頭的暫存檔、寫完再改名**:wiki-feeder 略過「.」開頭的檔,拿到的不是沒有
    就是完整的一份。
(3) **按了才送**,沒有自動送。
(4) **撞名**(手機版 2026-10-21):同名的是這台上次送的、還沒被收走 → 換成新版;內容一樣 →
    當成送過;同名但不是這台送的 → 不蓋掉,講原因。所以要記「送過什麼」(`record_file`)。
(5) **已經被收走又要送** → 知識庫會多一份(wiki-feeder 以內容判重,改過就是新內容),
    介面先問再送(`status` 回 `COLLECTED*`)。

⚠️ **送出資料夾不能跟存放位置、聲紋同步資料夾、App 自己的資料夾重疊**(`check_folder`):
前者重疊就是每份逐字稿與錄音檔一做好就被收走;後兩者裡面有聲紋、真名與會議紀錄草稿。
⚠️ **送出紀錄放 App 自己的資料夾**,不進 `data/`、不進 repo:裡面是逐字稿的完整路徑。
"""
import hashlib
import json
import logging
import os
import re
import wave
from datetime import datetime
from pathlib import Path

from meeting_scribe import export, outdir, paths, settings
from meeting_scribe.errors import UserFacingError
from meeting_scribe.i18n import t

logger = logging.getLogger(__name__)

# `status` 的回答
UNSENT = "unsent"                       # 沒送過(或送過的那份紀錄已經對不上這個檔)
WAITING = "waiting"                     # 送了、還在送出資料夾裡等著被收
WAITING_CHANGED = "waiting_changed"     # 送了、還沒被收,而這份之後又改過
COLLECTED = "collected"                 # 送了、已經被收走
COLLECTED_CHANGED = "collected_changed"  # 被收走之後這份又改過

# 「內容很少,還要送出嗎？」的門檻(同手機版 §8.11,2026-10-15 放寬後的值)
SHORT_SECONDS = 60
SHORT_CHARS = 100

# 暫存檔的前綴與後綴:「.」開頭 wiki-feeder 不收(雲端硬碟也多半不同步)
_TMP_PREFIX = "."
_TMP_SUFFIX = ".sending"


def folder() -> Path | None:
    """使用者選的送出資料夾;沒選(或設定檔裡的值不像一條絕對路徑)回 None。"""
    raw = settings.get(settings.KEY_SEND_DIR)
    if not isinstance(raw, str) or not raw.strip():
        return None
    path = Path(raw).expanduser()
    return path if path.is_absolute() else None


def choose(path) -> str | None:
    """記住送出資料夾;不能用就不記、回一句給使用者看的話。"""
    why = check_folder(path)
    if why is None:
        settings.set(settings.KEY_SEND_DIR, str(Path(path).expanduser().resolve()))
    return why


def reset() -> None:
    """「不送出」:清掉送出資料夾(送出鈕跟著消失)。"""
    settings.set(settings.KEY_SEND_DIR, None)


def _resolve(path) -> Path | None:
    try:
        return Path(path).expanduser().resolve()
    except (OSError, RuntimeError, TypeError, ValueError):
        return None


def overlaps(a, b) -> bool:
    """兩個資料夾是同一個、或一個在另一個底下。"""
    pa, pb = _resolve(a), _resolve(b)
    if pa is None or pb is None:
        return False
    return pa == pb or pa in pb.parents or pb in pa.parents


def check_folder(path) -> str | None:
    """這個資料夾能不能當送出資料夾;不行回一句給使用者看的話,可以回 None。

    ⚠️ 擋四種(理由見模組說明):不存在/寫不進去、跟存放位置重疊、跟聲紋同步資料夾重疊、
    跟 App 自己的資料夾重疊。⚠️ **存放位置兩個都要問**(指定的與預設的):指定的寫不進去時
    那一趟會退回預設(`outdir.for_run`),預設在送出資料夾裡一樣是整批被收走。"""
    from meeting_scribe import vpsync  # vpsync 也要問這裡(反方向那一道),避開循環 import

    p = _resolve(path)
    if p is None:
        return t("這個路徑讀不懂,請重新選一次。")
    if not p.is_dir():
        return t("找不到這個資料夾:{p}", p=outdir.display(p))
    for out in {outdir.current(), outdir.default_dir()}:
        if overlaps(p, out):
            return t("LLM Wiki 送出資料夾不能跟存放位置「{v}」重疊:每份逐字稿與錄音檔一做好就會被收走。"
                     "請另選一個資料夾,或先在上面改存放位置。", v=outdir.display(out))
    sync = vpsync.folder()
    if sync is not None and overlaps(p, sync):
        return t("LLM Wiki 送出資料夾不能跟聲紋同步資料夾重疊:那裡的聲紋與真名會被送進知識庫。"
                 "請另選一個資料夾。")
    if overlaps(p, paths.appdata_root()):
        return t("不能選工具自己的資料夾:裡面有聲紋、名單與會議紀錄草稿。請另選一個資料夾。")
    probe = p / f"{_TMP_PREFIX}write-test{_TMP_SUFFIX}"
    try:
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError:
        return t("這個資料夾寫不進去:{p}", p=outdir.display(p))
    return None


# ================================================================ 送出紀錄


def record_file() -> Path:
    """送過什麼(來源 md → 送到哪、送的內容雜湊)。**是函式不是常數**:測試 monkeypatch 它。"""
    return paths.appdata_root() / "sent.json"


def _load() -> dict:
    try:
        raw = json.loads(record_file().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception:
        logger.warning("送出紀錄讀不出來,這一次當成沒有", exc_info=True)
        return {}
    return raw if isinstance(raw, dict) else {}


def _save(data: dict) -> None:
    """先寫暫存檔再 `os.replace`(同 `settings.set`):寫到一半斷掉不會留下半份。"""
    path = record_file()
    tmp = path.with_suffix(".json.tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _key(md) -> str:
    return str(_resolve(md) or Path(md))


def _sha(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def status(md) -> str:
    """這份 md 的送出狀態(介面拿它決定按鈕上的字、要不要先問)。

    ⚠️ **只看「這台送過的那一份」還在不在**:wiki-feeder 收件、驗過雜湊就刪掉,所以「不見了」
    就是「收走了」——不需要任何通訊(同手機版「Mac 已收件」)。"""
    rec = _load().get(_key(md))
    if not isinstance(rec, dict) or not isinstance(rec.get("dest"), str):
        return UNSENT
    dest = Path(rec["dest"])
    changed = _sha(Path(md)) != rec.get("sha")
    # 同名的換成了別的內容(別人送的、或有人手動改過)也算「這台送的那份已經不在了」
    if dest.exists() and _sha(dest) == rec.get("sha"):
        return WAITING_CHANGED if changed else WAITING
    return COLLECTED_CHANGED if changed else COLLECTED


def send(md) -> Path:
    """把 `md` 送進送出資料夾,回送到的路徑。不能送就丟 `UserFacingError`(畫面原樣顯示)。

    ⚠️ **只送這一個檔**(契約 (1));⚠️ **暫存名寫完一次改名**(契約 (2)):`os.replace` 在
    同一顆磁碟上是原子的,wiki-feeder 拿到的不是舊的就是新的。"""
    target_dir = folder()
    if target_dir is None:
        raise UserFacingError(t("還沒有設定 LLM Wiki 送出資料夾(在「⚙ 進階參數設定」最後一列)。"))
    why = check_folder(target_dir)
    if why is not None:
        raise UserFacingError(why)
    src = Path(md)
    try:
        data = src.read_bytes()
    except OSError as e:
        logger.warning("要送出的逐字稿讀不到:%s", src, exc_info=True)
        raise UserFacingError(t("讀不到這份逐字稿:{p}", p=outdir.display(src))) from e
    sha = hashlib.sha256(data).hexdigest()
    dest = target_dir / src.name
    records = _load()
    rec = records.get(_key(src))
    if dest.exists():
        there = _sha(dest)
        ours = (isinstance(rec, dict) and rec.get("dest") == str(dest)
                and there is not None and there == rec.get("sha"))
        if there == sha:
            pass  # 內容一樣:當成送過,只補紀錄
        elif not ours:
            raise UserFacingError(t(
                "LLM Wiki 送出資料夾裡已經有一份同名的「{n}」,而且不是這台上次送的,所以沒有蓋掉它。"
                "請先把那份移走,或把這份逐字稿改名再送。", n=src.name))
    if not dest.exists() or _sha(dest) != sha:
        tmp = target_dir / f"{_TMP_PREFIX}{src.name}{_TMP_SUFFIX}"
        try:
            tmp.write_bytes(data)
            os.replace(tmp, dest)
        except OSError as e:
            logger.warning("送出失敗:%s → %s", src, dest, exc_info=True)
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise UserFacingError(t("送不進 LLM Wiki 送出資料夾「{p}」:{e}",
                                    p=outdir.display(target_dir), e=e.strerror or e)) from e
    records[_key(src)] = {"dest": str(dest), "sha": sha,
                          "at": datetime.now().isoformat(timespec="seconds")}
    try:
        _save(records)
    except OSError:
        # 檔案已經送到了;紀錄寫不進去只影響「已收件」那個標籤與下次重送的判斷
        logger.warning("送出紀錄寫不進去", exc_info=True)
    logger.info("已送出:%s → %s", src, dest)
    return dest


# ================================================================ 內容很少先問


_PUNCT = re.compile(r"[\s\W_]+", re.UNICODE)


def body_chars(md_text: str) -> int:
    r"""逐字稿正文的字數:只算講者行底下那幾行,不含檔頭、標題、講者行、備註、診斷表與標點。

    ⚠️ **講者行一律用 `export.SPEAKER_LINE_RE`**(全 repo 只有那一份)。"""
    total = 0
    in_turn = False
    for line in md_text.splitlines():
        s = line.strip()
        if export.SPEAKER_LINE_RE.match(s):
            in_turn = True
            continue
        if not s:
            in_turn = False
            continue
        if in_turn and not s.startswith(">"):
            total += len(_PUNCT.sub("", s))
    return total


def audio_seconds(md) -> float | None:
    r"""逐字稿旁邊同名 `.wav` 的長度(現場收音的成品就放在 md 旁邊);沒有或讀不動回 None。

    ⚠️ **檔案轉檔那條沒有**(原檔在別處):那種就只看字數。"""
    wav = Path(md).with_suffix(".wav")
    try:
        with wave.open(str(wav), "rb") as w:
            rate = w.getframerate()
            return w.getnframes() / rate if rate else None
    except (OSError, EOFError, wave.Error):
        return None


def too_little(md) -> bool:
    """要不要先問「內容很少,還要送出嗎？」:錄音不到 1 分鐘,或正文不到 100 字。"""
    secs = audio_seconds(md)
    if secs is not None and secs < SHORT_SECONDS:
        return True
    try:
        text = Path(md).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return body_chars(text) < SHORT_CHARS
