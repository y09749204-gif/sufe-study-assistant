"""Isolated Microsoft Office to PDF conversion for local academic previews."""
from __future__ import annotations

import os
import sys
from pathlib import Path




def disable_macros(application) -> None:
    try:
        application.AutomationSecurity = 3  # msoAutomationSecurityForceDisable
    except Exception:
        pass


def convert(source: Path, destination: Path) -> None:
    import pythoncom
    import win32com.client
    suffix = source.suffix.lower()
    application = document = None
    pythoncom.CoInitialize()
    try:
        if suffix in {".doc", ".docx"}:
            application = win32com.client.DispatchEx("Word.Application")
            application.Visible = False
            application.DisplayAlerts = 0
            disable_macros(application)
            document = application.Documents.Open(str(source), ConfirmConversions=False, ReadOnly=True,
                AddToRecentFiles=False, Visible=False, OpenAndRepair=True)
            if destination.suffix.lower() == ".docx" and suffix == ".doc":
                document.SaveAs2(str(destination), FileFormat=16)  # wdFormatDocumentDefault
            else:
                document.ExportAsFixedFormat(str(destination), 17)
        elif suffix in {".ppt", ".pptx"}:
            application = win32com.client.DispatchEx("PowerPoint.Application")
            disable_macros(application)
            document = application.Presentations.Open(str(source), ReadOnly=True, Untitled=False, WithWindow=False)
            document.SaveAs(str(destination), 32)  # ppSaveAsPDF
        elif suffix in {".xls", ".xlsx"}:
            application = win32com.client.DispatchEx("Excel.Application")
            application.Visible = False
            application.DisplayAlerts = False
            disable_macros(application)
            document = application.Workbooks.Open(str(source), UpdateLinks=0, ReadOnly=True, AddToMru=False,
                IgnoreReadOnlyRecommended=True)
            document.ExportAsFixedFormat(0, str(destination))
        else:
            raise ValueError(f"Unsupported Office preview type: {suffix}")
    finally:
        if document is not None:
            try:
                document.Close(False)
            except Exception:
                pass
        if application is not None:
            try:
                application.Quit()
            except Exception:
                pass
        pythoncom.CoUninitialize()


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: office_preview.py SOURCE DESTINATION")
    source = Path(sys.argv[1]).resolve(strict=True)
    destination = Path(sys.argv[2]).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f"{destination.stem}.{os.getpid()}.part{destination.suffix}")
    try:
        try:
            convert(source, temporary)
        except Exception:
            from personal_os_api.components import soffice
            import subprocess,tempfile,shutil
            executable=soffice()
            if not executable: raise RuntimeError("请安装 Microsoft Office 或可选 LibreOffice 转换组件") from None
            with tempfile.TemporaryDirectory() as folder:
                profile=Path(folder)/"profile"
                subprocess.run([executable,"-env:UserInstallation="+profile.as_uri(),"--headless","--convert-to","pdf","--outdir",folder,str(source)],check=True,timeout=120,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
                shutil.copyfile(Path(folder)/(source.stem+".pdf"),temporary)
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise RuntimeError("Office did not create a preview")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
