"""PySide6 instructor station GUI (spec §7.1).

The single main window with left-sidebar tab navigation (Status, Band & Radios,
Instructor, AAR, Settings, About). The GUI talks to the shared
:class:`~pivot.runtime.manager.SessionManager` in-process and never goes through
HTTP. PySide6 (LGPL-3.0) is the ``gui`` extra and is imported only when the GUI
actually runs, so headless deployments and CI do not require it.
"""
