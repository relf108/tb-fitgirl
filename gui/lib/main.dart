import 'package:flutter/material.dart';

import 'keyring.dart';
import 'screens/home_screen.dart';
import 'screens/setup_screen.dart';

void main() {
  runApp(const TbfgApp());
}

class TbfgApp extends StatelessWidget {
  const TbfgApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'tb-fitgirl',
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(
          seedColor: Colors.deepPurple,
          brightness: Brightness.dark,
        ),
        useMaterial3: true,
      ),
      home: const RootScreen(),
    );
  }
}

/// Routes to first-run setup when no API key is stored, else the main UI.
class RootScreen extends StatefulWidget {
  const RootScreen({super.key});

  @override
  State<RootScreen> createState() => _RootScreenState();
}

class _RootScreenState extends State<RootScreen> {
  static const _store = ApiKeyStore();

  String? _apiKey;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _loadKey();
  }

  Future<void> _loadKey() async {
    final key = await _store.load();
    if (!mounted) return;
    setState(() {
      _apiKey = key;
      _loading = false;
    });
  }

  void _onKeyChanged(String? key) {
    setState(() => _apiKey = key);
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) {
      return const Scaffold(body: Center(child: CircularProgressIndicator()));
    }
    final key = _apiKey;
    if (key == null) {
      return SetupScreen(onSaved: _onKeyChanged);
    }
    return HomeScreen(apiKey: key, onKeyChanged: _onKeyChanged);
  }
}
