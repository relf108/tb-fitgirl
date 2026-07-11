import 'dart:io';

import 'package:flutter/material.dart';

import '../bridge.dart';

/// Games installed by this tool (from our Steam shortcuts + launcher
/// entries), with one-click uninstall. Regular Steam games never appear.
class LibraryScreen extends StatefulWidget {
  const LibraryScreen({super.key});

  @override
  State<LibraryScreen> createState() => _LibraryScreenState();
}

class _LibraryScreenState extends State<LibraryScreen> {
  List<Map<String, dynamic>>? _games;
  bool _steamRunning = false;
  String? _error;
  String? _busyPath; // path of the game being uninstalled

  @override
  void initState() {
    super.initState();
    _refresh();
  }

  Future<void> _refresh() async {
    setState(() {
      _games = null;
      _error = null;
    });
    try {
      final data = await runBridgeOp('library', const {});
      if (!mounted) return;
      setState(() {
        _games = (data['games'] as List<dynamic>? ?? [])
            .whereType<Map<String, dynamic>>()
            .toList();
        _steamRunning = data['steam_running'] as bool? ?? false;
      });
    } on BridgeException catch (err) {
      if (!mounted) return;
      setState(() => _error = err.message);
    }
  }

  Future<void> _confirmUninstall(Map<String, dynamic> game) async {
    final name = game['name'] as String? ?? '';
    final installed = game['installed'] as bool? ?? false;
    var deleteFiles = installed;
    final proceed = await showDialog<bool>(
      context: context,
      builder: (context) => StatefulBuilder(
        builder: (context, setDialogState) => AlertDialog(
          title: Text('Uninstall $name?'),
          content: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text('Removes the Steam shortcut and launcher entry.'),
              if (_steamRunning) ...[
                const SizedBox(height: 8),
                const Text(
                  'Steam is RUNNING: close it first, or it will restore '
                  'the shortcut on exit.',
                  style: TextStyle(fontWeight: FontWeight.bold),
                ),
              ],
              if (installed)
                CheckboxListTile(
                  contentPadding: EdgeInsets.zero,
                  title: const Text('Also delete the game files'),
                  subtitle: Text(game['path'] as String? ?? ''),
                  value: deleteFiles,
                  onChanged: (v) =>
                      setDialogState(() => deleteFiles = v ?? false),
                ),
            ],
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(context).pop(false),
              child: const Text('Cancel'),
            ),
            FilledButton(
              style: FilledButton.styleFrom(
                backgroundColor: Theme.of(context).colorScheme.error,
              ),
              onPressed: () => Navigator.of(context).pop(true),
              child: const Text('Uninstall'),
            ),
          ],
        ),
      ),
    );
    if (proceed != true || !mounted) return;
    await _uninstall(game, deleteFiles: deleteFiles);
  }

  Future<void> _uninstall(
    Map<String, dynamic> game, {
    required bool deleteFiles,
  }) async {
    final path = game['path'] as String? ?? '';
    setState(() => _busyPath = path);
    try {
      final data = await runBridgeOp('uninstall', {
        'target': path,
        'keep_files': !deleteFiles,
      });
      if (!mounted) return;
      final deleted = data['deleted'] as bool? ?? false;
      _showSnack(
        'Uninstalled ${data['name']}'
        '${deleted ? ' and deleted its files' : ' (files kept)'}.',
      );
      await _refresh();
    } on BridgeException catch (err) {
      if (!mounted) return;
      _showSnack(err.message);
    } finally {
      if (mounted) setState(() => _busyPath = null);
    }
  }

  void _showSnack(String message) {
    ScaffoldMessenger.of(context)
        .showSnackBar(SnackBar(content: Text(message)));
  }

  Widget _buildGame(Map<String, dynamic> game) {
    final name = game['name'] as String? ?? '';
    final installed = game['installed'] as bool? ?? false;
    final hasShortcut = game['steam_shortcut'] as bool? ?? false;
    final hasEntry = game['launcher_entry'] as bool? ?? false;
    final busy = _busyPath == game['path'];
    final badges = <String>[
      if (hasShortcut) 'Steam',
      if (hasEntry) 'Launcher',
      if (!installed) 'files missing',
    ];
    final icon = game['icon'] as String?;
    final fallback = Icon(
      installed ? Icons.videogame_asset : Icons.videogame_asset_off,
      color: installed ? null : Theme.of(context).colorScheme.error,
    );
    return ListTile(
      leading: icon != null
          ? SizedBox(
              width: 87,
              height: 41,
              child: ClipRRect(
                borderRadius: BorderRadius.circular(4),
                child: Image.file(
                  File(icon),
                  fit: BoxFit.cover,
                  errorBuilder: (_, __, ___) => fallback,
                ),
              ),
            )
          : fallback,
      title: Text(name),
      subtitle: Text(badges.join('  -  ')),
      trailing: busy
          ? const SizedBox(
              height: 20,
              width: 20,
              child: CircularProgressIndicator(strokeWidth: 2),
            )
          : OutlinedButton.icon(
              icon: const Icon(Icons.delete_outline),
              label: const Text('Uninstall'),
              onPressed:
                  _busyPath != null ? null : () => _confirmUninstall(game),
            ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final games = _games;
    final Widget body;
    if (_error != null) {
      body = Center(
        child: Text(
          _error!,
          style: TextStyle(color: Theme.of(context).colorScheme.error),
        ),
      );
    } else if (games == null) {
      body = const Center(child: CircularProgressIndicator());
    } else if (games.isEmpty) {
      body = const Center(
        child: Text('No games installed by tb-fitgirl yet.'),
      );
    } else {
      body = ListView(
        children: [
          if (_steamRunning)
            const Padding(
              padding: EdgeInsets.all(12),
              child: Text(
                'Steam is running: close it before uninstalling, or it will '
                'restore shortcuts on exit.',
                style: TextStyle(fontWeight: FontWeight.bold),
              ),
            ),
          ...games.map(_buildGame),
        ],
      );
    }
    return Scaffold(
      appBar: AppBar(
        title: const Text('Library'),
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh),
            tooltip: 'Refresh',
            onPressed: _refresh,
          ),
        ],
      ),
      body: body,
    );
  }
}
