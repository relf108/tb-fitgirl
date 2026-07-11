import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:tbfg_gui/screens/setup_screen.dart';

void main() {
  testWidgets('setup screen renders the API key prompt', (tester) async {
    await tester.pumpWidget(MaterialApp(home: SetupScreen(onSaved: (_) {})));
    expect(find.text('Enter your TorBox API key'), findsOneWidget);
    expect(find.text('Validate & save'), findsOneWidget);
    expect(find.byType(TextField), findsOneWidget);
  });
}
