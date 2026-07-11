import 'package:flutter/material.dart';

import '../bridge.dart';
import '../keyring.dart';
import '../models.dart';

/// Update, validate, or clear the stored TorBox API key.
class SettingsScreen extends StatefulWidget {
  const SettingsScreen({
    super.key,
    required this.apiKey,
    required this.onKeyChanged,
  });

  final String apiKey;
  final void Function(String? key) onKeyChanged;

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  static const _store = ApiKeyStore();

  final _controller = TextEditingController();
  bool _busy = false;
  AccountInfo? _account;
  String? _error;

  @override
  void initState() {
    super.initState();
    _checkCurrent();
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  String get _maskedKey {
    final key = widget.apiKey;
    if (key.length <= 8) return '****';
    return '${key.substring(0, 4)}...${key.substring(key.length - 4)}';
  }

  Future<void> _checkCurrent() async {
    try {
      final data = await runBridgeOp('validate_key', {
        'api_key': widget.apiKey,
      });
      if (!mounted) return;
      setState(() => _account = AccountInfo.fromJson(data));
    } on BridgeException catch (err) {
      if (!mounted) return;
      setState(() => _error = 'Current key check failed: ${err.message}');
    }
  }

  Future<void> _updateKey() async {
    final key = _controller.text.trim();
    if (key.isEmpty) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final data = await runBridgeOp('validate_key', {'api_key': key});
      final stored = await _store.save(key);
      if (!mounted) return;
      setState(() => _account = AccountInfo.fromJson(data));
      widget.onKeyChanged(key);
      _controller.clear();
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            stored
                ? 'API key updated.'
                : 'Key valid, but keyring write failed.',
          ),
        ),
      );
    } on BridgeException catch (err) {
      if (!mounted) return;
      setState(() => _error = err.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _clearKey() async {
    await _store.clear();
    if (!mounted) return;
    widget.onKeyChanged(null);
    Navigator.of(context).pop();
  }

  @override
  Widget build(BuildContext context) {
    final account = _account;
    return Scaffold(
      appBar: AppBar(title: const Text('Settings')),
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 480),
          child: ListView(
            padding: const EdgeInsets.all(24),
            children: [
              const Text(
                'TorBox account',
                style: TextStyle(fontWeight: FontWeight.bold),
              ),
              const SizedBox(height: 8),
              Text('API key: $_maskedKey'),
              if (account != null)
                Text(
                  '${account.email} - ${account.planName}, expires ${account.expiry}',
                ),
              const Divider(height: 32),
              TextField(
                controller: _controller,
                obscureText: true,
                decoration: const InputDecoration(
                  labelText: 'New API key',
                  border: OutlineInputBorder(),
                ),
                onSubmitted: (_) => _updateKey(),
              ),
              const SizedBox(height: 12),
              FilledButton(
                onPressed: _busy ? null : _updateKey,
                child: const Text('Validate & update'),
              ),
              const SizedBox(height: 12),
              OutlinedButton(
                onPressed: _busy ? null : _clearKey,
                child: const Text('Clear stored key'),
              ),
              if (_error != null) ...[
                const SizedBox(height: 16),
                Text(
                  _error!,
                  style: TextStyle(color: Theme.of(context).colorScheme.error),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}
