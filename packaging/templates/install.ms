-- dcc-mcp-3dsmax {{VERSION}} drag-and-drop installer.
(
    fn _dccMcpNormalizePath value =
    (
        substituteString value "\\" "/"
    )

    local sourceRoot = _dccMcpNormalizePath (getFilenamePath (getSourceFileName()))
    local userScripts = _dccMcpNormalizePath (getDir #userScripts)
    local userStartupScripts = _dccMcpNormalizePath (getDir #userStartupScripts)
    fn _dccMcpInstall =
    (
        local py = ""
        py += "from pathlib import Path\n"
        py += "import os, shutil, sys, time, uuid\n"
        py += "source = Path(r'''" + sourceRoot + "''')\n"
        py += "install_dir = Path(r'''" + userScripts + "''') / 'dcc_mcp_3dsmax'\n"
        py += "versions_dir = install_dir / 'versions'\n"
        py += "current_file = install_dir / 'current.txt'\n"
        py += "uninstall_marker = Path(r'''" + userScripts + "''') / 'dcc_mcp_3dsmax_uninstall_pending'\n"
        py += "legacy_uninstall_marker = install_dir / 'dcc_mcp_3dsmax_uninstall_pending'\n"
        py += "uninstall_markers = [uninstall_marker, legacy_uninstall_marker]\n"
        py += "startup_dir = Path(r'''" + userStartupScripts + "''')\n"
        py += "startup_script = startup_dir / 'dcc_mcp_3dsmax_startup.ms'\n"
        py += "version_name = '{{VERSION}}'\n"
        py += "def _pkg(root):\n"
        py += "    return root / ('python37' if sys.version_info < (3, 8) else 'python')\n"
        py += "def _clear_path(path, allow_restart=False):\n"
        py += "    if not path.exists():\n"
        py += "        return {'success': True, 'status': 'skipped'}\n"
        py += "    if path.is_file() or path.is_symlink():\n"
        py += "        path.unlink()\n"
        py += "        return {'success': True, 'status': 'removed'}\n"
        py += "    try:\n"
        py += "        from dcc_mcp_core.install_lifecycle import safe_remove_tree\n"
        py += "    except Exception:\n"
        py += "        shutil.rmtree(path)\n"
        py += "        return {'success': True, 'status': 'removed'}\n"
        py += "    result = safe_remove_tree(path)\n"
        py += "    if result.get('requires_restart') and allow_restart:\n"
        py += "        return result\n"
        py += "    if not result.get('success'):\n"
        py += "        raise RuntimeError(result.get('message') or result.get('status') or 'remove failed')\n"
        py += "    return result\n"
        py += "def _version_key():\n"
        py += "    safe = ''.join(ch if ch.isalnum() or ch in '._-' else '_' for ch in version_name) or 'dev'\n"
        py += "    return safe + '_' + time.strftime('%Y%m%d%H%M%S') + '_' + uuid.uuid4().hex[:8]\n"
        py += "def _active_root():\n"
        py += "    if current_file.exists():\n"
        py += "        current = current_file.read_text(encoding='utf-8').strip()\n"
        py += "        candidate = versions_dir / current\n"
        py += "        if current and candidate.exists():\n"
        py += "            return candidate\n"
        py += "    return install_dir\n"
        py += "def _copy_payload(target):\n"
        py += "    shutil.copytree(source / 'payload', target)\n"
        py += "def _copy_startup(root):\n"
        py += "    startup_dir.mkdir(parents=True, exist_ok=True)\n"
        py += "    shutil.copy2(root / 'startup' / 'dcc_mcp_3dsmax_startup.ms', startup_script)\n"
        py += "def _stop_existing(action):\n"
        py += "    current_root = _active_root()\n"
        py += "    if not current_root.exists():\n"
        py += "        return\n"
        py += "    pkg = _pkg(current_root)\n"
        py += "    if pkg.exists() and str(pkg) not in sys.path:\n"
        py += "        sys.path.insert(0, str(pkg))\n"
        py += "    try:\n"
        py += "        import dcc_mcp_3dsmax\n"
        py += "    except ModuleNotFoundError:\n"
        py += "        return\n"
        py += "    try:\n"
        py += "        dcc_mcp_3dsmax.stop_sidecar_bridge()\n"
        py += "    except Exception as exc:\n"
        py += "        raise RuntimeError('Failed to stop dcc-mcp-3dsmax sidecar before ' + action + ': %s' % exc) from exc\n"
        py += "def _clear_uninstall_markers():\n"
        py += "    for marker in uninstall_markers:\n"
        py += "        try:\n"
        py += "            if marker.exists():\n"
        py += "                marker.unlink()\n"
        py += "        except OSError:\n"
        py += "            pass\n"
        py += "def _clear_adapter_modules():\n"
        py += "    for name in list(sys.modules):\n"
        py += "        if name == 'dcc_mcp_3dsmax' or name.startswith('dcc_mcp_3dsmax.'):\n"
        py += "            sys.modules.pop(name, None)\n"
        py += "def _cleanup_obsolete_payloads(active_key):\n"
        py += "    active_root = versions_dir / active_key\n"
        py += "    pkg = _pkg(active_root)\n"
        py += "    if pkg.exists() and str(pkg) not in sys.path:\n"
        py += "        sys.path.insert(0, str(pkg))\n"
        py += "    obsolete = []\n"
        py += "    if versions_dir.exists():\n"
        py += "        obsolete.extend(path for path in versions_dir.iterdir() if path.name != active_key and not path.name.endswith('.installing'))\n"
        py += "    if install_dir.exists():\n"
        py += "        for path in install_dir.iterdir():\n"
        py += "            if path.name in ('versions', 'current.txt'):\n"
        py += "                continue\n"
        py += "            if path in uninstall_markers:\n"
        py += "                continue\n"
        py += "            obsolete.append(path)\n"
        py += "    for path in obsolete:\n"
        py += "        try:\n"
        py += "            result = _clear_path(path, allow_restart=True)\n"
        py += "            if result.get('requires_restart'):\n"
        py += "                print('Old dcc-mcp-3dsmax payload cleanup requires restart:', path, result.get('message'))\n"
        py += "        except Exception as exc:\n"
        py += "            print('Failed to cleanup old dcc-mcp-3dsmax payload:', path, exc)\n"
        py += "def _activate_runtime(root):\n"
        py += "    _clear_adapter_modules()\n"
        py += "    pkg = _pkg(root)\n"
        py += "    if str(pkg) not in sys.path:\n"
        py += "        sys.path.insert(0, str(pkg))\n"
        py += "    os.environ.setdefault('DCC_MCP_SERVER_ROOT', str(pkg))\n"
        py += "    import dcc_mcp_3dsmax\n"
        py += "    dcc_mcp_3dsmax.remove_menu()\n"
        py += "    dcc_mcp_3dsmax.install_menu()\n"
        py += "    dcc_mcp_3dsmax.install_shutdown_callback()\n"
        py += "    dcc_mcp_3dsmax.main()\n"
        py += "_stop_existing('install')\n"
        py += "_clear_adapter_modules()\n"
        py += "install_dir.mkdir(parents=True, exist_ok=True)\n"
        py += "versions_dir.mkdir(parents=True, exist_ok=True)\n"
        py += "_clear_uninstall_markers()\n"
        py += "key = _version_key()\n"
        py += "version_dir = versions_dir / key\n"
        py += "staging_dir = versions_dir / (key + '.installing')\n"
        py += "_clear_path(staging_dir)\n"
        py += "_copy_payload(staging_dir)\n"
        py += "staging_dir.rename(version_dir)\n"
        py += "current_file.write_text(key + '\\n', encoding='utf-8')\n"
        py += "_copy_startup(version_dir)\n"
        py += "_cleanup_obsolete_payloads(key)\n"
        py += "_activate_runtime(version_dir)\n"
        py += "print('Installed dcc-mcp-3dsmax', version_name, 'to', version_dir)\n"
        try
        (
            python.Execute py
            messageBox "dcc-mcp-3dsmax installed and runtime startup requested." title:"dcc-mcp-3dsmax"
        )
        catch
        (
            messageBox ("dcc-mcp-3dsmax install failed:\n" + getCurrentException()) title:"dcc-mcp-3dsmax"
        )
    )

    fn _dccMcpUninstall =
    (
        local py = ""
        py += "from pathlib import Path\n"
        py += "import shutil, sys\n"
        py += "source = Path(r'''" + sourceRoot + "''')\n"
        py += "install_dir = Path(r'''" + userScripts + "''') / 'dcc_mcp_3dsmax'\n"
        py += "versions_dir = install_dir / 'versions'\n"
        py += "current_file = install_dir / 'current.txt'\n"
        py += "uninstall_marker = Path(r'''" + userScripts + "''') / 'dcc_mcp_3dsmax_uninstall_pending'\n"
        py += "legacy_uninstall_marker = install_dir / 'dcc_mcp_3dsmax_uninstall_pending'\n"
        py += "uninstall_markers = [uninstall_marker, legacy_uninstall_marker]\n"
        py += "startup_dir = Path(r'''" + userStartupScripts + "''')\n"
        py += "startup_script = Path(r'''" + userStartupScripts + "''') / 'dcc_mcp_3dsmax_startup.ms'\n"
        py += "def _active_root():\n"
        py += "    if current_file.exists():\n"
        py += "        current = current_file.read_text(encoding='utf-8').strip()\n"
        py += "        candidate = versions_dir / current\n"
        py += "        if current and candidate.exists():\n"
        py += "            return candidate\n"
        py += "    return install_dir\n"
        py += "pkg = _active_root() / ('python37' if sys.version_info < (3, 8) else 'python')\n"
        py += "if str(pkg) not in sys.path:\n"
        py += "    sys.path.insert(0, str(pkg))\n"
        py += "try:\n"
        py += "    import dcc_mcp_3dsmax\n"
        py += "    dcc_mcp_3dsmax.stop_sidecar_bridge()\n"
        py += "    dcc_mcp_3dsmax.remove_menu()\n"
        py += "except ModuleNotFoundError:\n"
        py += "    pass\n"
        py += "except Exception as exc:\n"
        py += "    raise RuntimeError('Failed to stop dcc-mcp-3dsmax sidecar before uninstall: {}'.format(exc)) from exc\n"
        py += "def _clear_adapter_modules():\n"
        py += "    for name in list(sys.modules):\n"
        py += "        if name == 'dcc_mcp_3dsmax' or name.startswith('dcc_mcp_3dsmax.'):\n"
        py += "            sys.modules.pop(name, None)\n"
        py += "def _clear_uninstall_markers():\n"
        py += "    for marker in uninstall_markers:\n"
        py += "        try:\n"
        py += "            if marker.exists():\n"
        py += "                marker.unlink()\n"
        py += "        except OSError:\n"
        py += "            pass\n"
        py += "def _clear_path(path):\n"
        py += "    if path.exists():\n"
        py += "        if path.is_file() or path.is_symlink():\n"
        py += "            path.unlink()\n"
        py += "            return {'success': True, 'status': 'removed'}\n"
        py += "        try:\n"
        py += "            from dcc_mcp_core.install_lifecycle import safe_remove_tree\n"
        py += "        except Exception:\n"
        py += "            shutil.rmtree(path)\n"
        py += "            return {'success': True, 'status': 'removed'}\n"
        py += "        result = safe_remove_tree(path)\n"
        py += "        if result.get('requires_restart'):\n"
        py += "            raise PermissionError(result.get('message') or result.get('recommended_next_action'))\n"
        py += "        if not result.get('success'):\n"
        py += "            raise RuntimeError(result.get('message') or result.get('status') or 'remove failed')\n"
        py += "        return result\n"
        py += "    return {'success': True, 'status': 'skipped'}\n"
        py += "try:\n"
        py += "    _clear_adapter_modules()\n"
        py += "    _clear_path(install_dir)\n"
        py += "    if startup_script.exists():\n"
        py += "        startup_script.unlink()\n"
        py += "    _clear_uninstall_markers()\n"
        py += "    print('Uninstalled dcc-mcp-3dsmax from', install_dir)\n"
        py += "except PermissionError as exc:\n"
        py += "    uninstall_marker.parent.mkdir(parents=True, exist_ok=True)\n"
        py += "    startup_dir.mkdir(parents=True, exist_ok=True)\n"
        py += "    uninstall_marker.write_text('pending\\n', encoding='utf-8')\n"
        py += "    shutil.copy2(source / 'payload' / 'startup' / 'dcc_mcp_3dsmax_startup.ms', startup_script)\n"
        py += "    print('dcc-mcp-3dsmax uninstall requires a 3ds Max restart; staged cleanup for', install_dir, 'because:', exc)\n"
        try
        (
            python.Execute py
            messageBox "dcc-mcp-3dsmax uninstall requested. Restart 3ds Max to finish if files were in use." title:"dcc-mcp-3dsmax"
        )
        catch
        (
            messageBox ("dcc-mcp-3dsmax uninstall failed:\n" + getCurrentException()) title:"dcc-mcp-3dsmax"
        )
    )

    rollout dccMcpInstaller "dcc-mcp-3dsmax" width:280 height:110
    (
        label infoLbl "Choose an action for dcc-mcp-3dsmax:" pos:[20,16]
        button installBtn "Install" width:110 height:28 pos:[20,50]
        button uninstallBtn "Uninstall" width:110 height:28 pos:[145,50]

        on installBtn pressed do
        (
            destroyDialog dccMcpInstaller
            _dccMcpInstall()
        )

        on uninstallBtn pressed do
        (
            destroyDialog dccMcpInstaller
            _dccMcpUninstall()
        )
    )

    createDialog dccMcpInstaller style:#(#style_titlebar, #style_border, #style_sysmenu)
)
