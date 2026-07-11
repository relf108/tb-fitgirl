import 'dart:async';

import 'package:flutter/material.dart';

import '../bridge.dart';
import '../models.dart';
import 'install_screen.dart';
import 'library_screen.dart';
import 'settings_screen.dart';

/// Search a repack source, show TorBox cache status, and launch installs.
class HomeScreen extends StatefulWidget {
  const HomeScreen({
    super.key,
    required this.apiKey,
    required this.onKeyChanged,
  });

  final String apiKey;
  final void Function(String? key) onKeyChanged;

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  static const _debounce = Duration(milliseconds: 600);

  final _controller = TextEditingController();
  Timer? _timer;
  int _searchSeq = 0;
  bool _searching = false;
  String _status = 'Type a title to search.';
  List<SearchResult> _results = const [];
  final Map<String, Future<String?>> _thumbnails = {};

  /// Lazy per-title store thumbnail lookup, cached for the app's lifetime.
  Future<String?> _thumbnailUrl(String title) {
    return _thumbnails.putIfAbsent(title, () async {
      try {
        final data = await runBridgeOp('metadata', {'name': title});
        return data['image'] as String?;
      } on BridgeException {
        return null;
      }
    });
  }

  Widget _thumbnail(SearchResult result) {
    const fallback = Icon(Icons.videogame_asset, size: 32);
    return SizedBox(
      width: 87,
      height: 33,
      child: FutureBuilder<String?>(
        future: _thumbnailUrl(result.title),
        builder: (context, snapshot) {
          final url = snapshot.data;
          if (url == null) return fallback;
          return ClipRRect(
            borderRadius: BorderRadius.circular(4),
            child: Image.network(
              url,
              fit: BoxFit.cover,
              errorBuilder: (_, __, ___) => fallback,
            ),
          );
        },
      ),
    );
  }

  @override
  void dispose() {
    _timer?.cancel();
    _controller.dispose();
    super.dispose();
  }

  void _onQueryChanged(String query) {
    _timer?.cancel();
    final trimmed = query.trim();
    if (trimmed.isEmpty) {
      setState(() {
        _results = const [];
        _status = 'Type a title to search.';
        _searching = false;
      });
      return;
    }
    _timer = Timer(_debounce, () => _search(trimmed));
  }

  Future<void> _search(String title) async {
    final seq = ++_searchSeq;
    setState(() {
      _searching = true;
      _status = 'Searching...';
    });
    try {
      // The bridge runs scrapes in its own process, off the UI thread.
      final data = await runBridgeOp(
        'search',
        {'title': title, 'limit': 10, 'api_key': widget.apiKey},
        onProgress: (p) {
          if (seq == _searchSeq && p.message.isNotEmpty && mounted) {
            setState(() => _status = p.message);
          }
        },
      );
      if (seq != _searchSeq || !mounted) return; // superseded by newer input
      final repacks = (data['repacks'] as List<dynamic>? ?? [])
          .whereType<Map<String, dynamic>>()
          .map(SearchResult.fromJson)
          .toList();
      setState(() {
        _results = repacks;
        _searching = false;
        _status = repacks.isEmpty
            ? 'No repacks found.'
            : '${repacks.length} result(s).';
      });
    } on BridgeException catch (err) {
      if (seq != _searchSeq || !mounted) return;
      setState(() {
        _searching = false;
        _status = 'Search failed: ${err.message}';
      });
    }
  }

  Future<void> _confirmInstall(SearchResult result) async {
    Map<String, dynamic> status = const {};
    try {
      status = await runBridgeOp('status', const {});
    } on BridgeException {
      // Non-fatal: proceed without the Steam pre-check.
    }
    if (!mounted) return;
    final steamRunning = status['steam_running'] as bool? ?? false;
    final proceed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text('Install ${result.title}?'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (steamRunning)
              const Text(
                'Steam is RUNNING. Close it before installing, or the '
                'Steam shortcut cannot be written.',
                style: TextStyle(fontWeight: FontWeight.bold),
              )
            else
              const Text('Steam appears closed - good.'),
            const SizedBox(height: 12),
            Text(
              result.cached
                  ? 'Cached on TorBox (${result.sizeHuman}): download starts immediately.'
                  : 'Not cached on TorBox: it will be fetched first, which can take a while.',
            ),
            const SizedBox(height: 12),
            const Text(
              'After install, set the Proton version in Steam under '
              'Properties > Compatibility.',
            ),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.of(context).pop(true),
            child: const Text('Install'),
          ),
        ],
      ),
    );
    if (proceed != true || !mounted) return;
    await Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => InstallScreen(
          title: result.title,
          source: result.source,
          apiKey: widget.apiKey,
        ),
      ),
    );
  }

  void _openSettings() {
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => SettingsScreen(
          apiKey: widget.apiKey,
          onKeyChanged: widget.onKeyChanged,
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('tb-fitgirl'),
        actions: [
          IconButton(
            icon: const Icon(Icons.videogame_asset),
            tooltip: 'Library',
            onPressed: () => Navigator.of(context).push(
              MaterialPageRoute<void>(builder: (_) => const LibraryScreen()),
            ),
          ),
          IconButton(
            icon: const Icon(Icons.settings),
            tooltip: 'Settings',
            onPressed: _openSettings,
          ),
        ],
      ),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.all(16),
            child: TextField(
              controller: _controller,
              autofocus: true,
              decoration: InputDecoration(
                labelText: 'Search repacks',
                hintText: 'e.g. pragmata',
                border: const OutlineInputBorder(),
                suffixIcon: _searching
                    ? const Padding(
                        padding: EdgeInsets.all(12),
                        child: SizedBox(
                          height: 16,
                          width: 16,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        ),
                      )
                    : const Icon(Icons.search),
              ),
              onChanged: _onQueryChanged,
            ),
          ),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16),
            child: Align(
              alignment: Alignment.centerLeft,
              child: Text(
                _status,
                style: Theme.of(context).textTheme.bodySmall,
              ),
            ),
          ),
          const SizedBox(height: 8),
          Expanded(
            child: ListView.builder(
              itemCount: _results.length,
              itemBuilder: (context, index) {
                final result = _results[index];
                return ListTile(
                  title: Text(result.title),
                  subtitle: Row(
                    children: [
                      Tooltip(
                        message: result.cached
                            ? 'Cached on TorBox'
                            : 'Not cached: TorBox must fetch it first',
                        child: Icon(
                          result.cached ? Icons.cloud_done : Icons.cloud_off,
                          size: 16,
                          color: result.cached ? Colors.green : null,
                        ),
                      ),
                      const SizedBox(width: 6),
                      Text(result.sizeHuman.isEmpty
                          ? (result.cached ? 'cached' : 'not cached')
                          : result.sizeHuman),
                    ],
                  ),
                  leading: _thumbnail(result),
                  trailing: FilledButton.icon(
                    icon: const Icon(Icons.download),
                    label: const Text('Install'),
                    onPressed: () => _confirmInstall(result),
                  ),
                );
              },
            ),
          ),
        ],
      ),
    );
  }
}
