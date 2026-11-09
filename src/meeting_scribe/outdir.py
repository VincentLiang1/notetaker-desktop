r"""逐字稿與錄音檔存在哪(「聲音→MD」那一頁的成品;文件轉檔的成品在原檔旁邊,不歸這裡管)。

(2026-11-09 使用者:朋友要拿錄音檔與 md,而 Mac 上預設的位置藏在 `~/Library` 底下。)
定案的四件事:
(1) **預設照平台**:Windows 照舊是工具資料夾的 `output`;Mac 是家目錄底下以**介面語言**
    命名的資料夾(`~/會議助手電腦版` 等),上層由 `plat.default_output_parent` 決定。
(2) **使用者可以自己指定**(「進階參數設定」的「存放位置」),記在 `settings`——它描述的是
    **這台電腦**,不是這一份工作。
(3) **指定的寫不進去就退回預設、照常開始**(`for_run`):錄音不能重來,會議開場時擋下來要他
    去改設定,代價比「這一趟換個地方存」大得多。設定本身不改,下次插回隨身碟就恢復。
(4) **Mac 上舊位置的檔案整個搬過去**(`migrate_legacy`,只在開窗那條路)。

⚠️ **錄音的工作目錄(`appdata_root()/recordings`)不歸這裡管**:那是錄音中的暫存,成品在收尾
時才複製到這裡;把它也搬到使用者看得到的地方,當機殘留就會被人當成垃圾刪掉。
⚠️ **會議紀錄草稿也不歸這裡管**(`minutes.drafts_dir`):使用者的逐字稿資料夾常常就是餵知識庫
的那一個,草稿不能進去。
"""
import logging
import os
import tempfile
from pathlib import Path

from meeting_scribe import brand, i18n, paths, pending, plat, settings
from meeting_scribe.i18n import t

logger = logging.getLogger(__name__)

#: 介面語言 → Mac 預設資料夾的名字(與 App 在 Finder 裡顯示的名字一致,一看就知道是誰的)。
#: ⚠️ **名字只在第一次建立時決定**(`default_dir`):之後換了介面語言,認的是**已經存在**的
#: 那一個——跟著語言改名的話,換一次語言檔案就「不見了」。
_NAMES = {
    i18n.ZH_TW: brand.APP_NAME,
    i18n.ZH_CN: brand.APP_NAME_ZH_CN,
    i18n.EN: brand.APP_NAME_EN,
}


def legacy_dir() -> Path:
    r"""工具資料夾裡的 `output`(Windows 的預設;Mac 在 2026-11-09 之前的位置)。

    ⚠️ **自動更新永遠不碰這個名字**(`update.NEVER_TOUCH`):Windows 上它就是成品本身。"""
    return paths.repo_root() / "output"


def default_dir() -> Path:
    r"""沒指定時存在哪。

    ⚠️ **Mac 上新位置還不存在、而舊位置有東西時,回舊的**(同 `paths.appdata_root` 那一段):
    命令列那條路不搬(`migrate_legacy` 只在開窗時跑),而開窗那邊萬一搬不動,程式要繼續用舊
    位置——不然同一台機器的成品就分成兩半,舊的那一半又是藏起來的。"""
    parent = plat.default_output_parent()
    if parent is None:
        return legacy_dir()
    parent = Path(parent)
    for name in _NAMES.values():
        if (parent / name).is_dir():
            return parent / name
    legacy = legacy_dir()
    if _has_files(legacy):
        return legacy
    return parent / _NAMES.get(i18n.current(), brand.APP_NAME_EN)


def custom() -> Path | None:
    r"""使用者指定的位置;沒指定(或設定檔裡的值不像一條路徑)回 None。

    ⚠️ **相對路徑一律不認**:設定檔可以手改,而相對路徑的意思會跟著「從哪裡啟動」變。"""
    raw = settings.get(settings.KEY_OUTPUT_DIR)
    if not isinstance(raw, str) or not raw.strip():
        return None
    path = Path(raw).expanduser()
    return path if path.is_absolute() else None


def current() -> Path:
    """現在的存放位置(指定的優先,沒指定就是預設)。不檢查寫不寫得進去,見 `for_run`。"""
    return custom() or default_dir()


def choose(path) -> None:
    """記住使用者挑的資料夾。挑的剛好是預設那一個就當成「沒指定」(之後預設改了才會跟著走)。"""
    path = Path(path)
    if path == default_dir():
        reset()
        return
    settings.set(settings.KEY_OUTPUT_DIR, str(path))


def reset() -> None:
    """恢復預設。"""
    settings.set(settings.KEY_OUTPUT_DIR, None)


def problem(path) -> str | None:
    r"""試寫一次:寫得進去回 None,寫不進去回原因(給記錄檔,不直接給使用者看)。

    ⚠️ **要真的寫一個檔,不能只問 `os.access`**:雲端硬碟沒掛上時資料夾可能還在、權限也對,
    寫下去才失敗;而這個問題要在按下「開始」時就答出來,不能等一小時後收尾才知道。"""
    path = Path(path)
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path, prefix=".write-test-"):
            pass
    except OSError as e:
        return str(e)
    return None


def for_run() -> tuple[Path, str | None]:
    r"""按下「開始」那一刻:這一趟存在哪,以及要不要跟使用者講一句(None = 不必講)。

    ⚠️ **只有「指定的」才會退回**:預設那一個寫不進去,已經沒有更好的地方可退,讓它在落地那
    一步照原本的錯誤處理走。⚠️ **設定不改**:隨身碟拔掉是暫時的,下次插回去就要恢復。"""
    chosen = custom()
    if chosen is None:
        return default_dir(), None
    why = problem(chosen)
    if why is None:
        return chosen, None
    fallback = default_dir()
    logger.warning("指定的存放位置寫不進去(%s:%s),這一趟改存到 %s", chosen, why, fallback)
    return fallback, t("存放位置「{v}」寫不進去,這一趟改存到「{d}」。",
                       v=display(chosen), d=display(fallback))


def display(path) -> str:
    """給人看的寫法:家目錄換成 `~`(Mac 的預設就是 `~/會議助手電腦版`,完整路徑只是把人名多印一次)。"""
    path = Path(path)
    try:
        rel = path.relative_to(Path.home())
    except ValueError:
        return str(path)
    return str(Path("~") / rel)


def _has_files(folder: Path) -> bool:
    try:
        return folder.is_dir() and any(folder.iterdir())
    except OSError:
        return False


def migrate_legacy() -> Path | None:
    r"""Mac:把舊位置(工具資料夾的 `output`)整個搬到家目錄底下的新位置;搬了回新位置。

    (2026-11-09 使用者選「自動搬過去」。)
    ⚠️ **只在開窗那條路呼叫**(同 `paths.migrate_legacy_appdata`):命令列可能在背景跑批次。
    ⚠️ **使用者指定過就不搬**:他挑的位置與舊位置無關,搬過去的是他沒要求的東西。
    ⚠️ **新位置已經在就不搬、不合併**:那是兩份成品,合併時同名檔要蓋掉哪一份,猜錯比留著糟。
    ⚠️ **同一顆磁碟所以用 `os.rename`**:原子、瞬間完成;跨磁碟丟 `OSError`,走到下面那條。
    ⚠️ **搬不動只記一行、絕不拋**:`default_dir` 會繼續回舊位置,程式照常跑。"""
    parent = plat.default_output_parent()
    if parent is None or custom() is not None:
        return None
    legacy = legacy_dir()
    if not _has_files(legacy):
        return None
    parent = Path(parent)
    if any((parent / name).exists() for name in _NAMES.values()):
        return None
    target = parent / _NAMES.get(i18n.current(), brand.APP_NAME_EN)
    try:
        os.rename(legacy, target)
    except OSError:
        logger.warning("逐字稿資料夾搬不動(%s → %s),繼續用舊位置", legacy, target,
                       exc_info=True)
        return None
    logger.info("逐字稿資料夾已搬到 %s(原本在 %s)", target, legacy)
    # 未完成的命名記的是絕對路徑,不跟著換的話下次開窗就被當成「成品被移走」清掉
    pending.relocate(legacy, target)
    return target
