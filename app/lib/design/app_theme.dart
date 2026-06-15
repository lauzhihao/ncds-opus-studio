import 'package:flutter/material.dart';
import 'tokens.dart';
import 'typography.dart';

/// 全局主题:仅亮色。语义靠暖色明度差,不提供 darkTheme。
class AppTheme {
  AppTheme._();

  static ThemeData get light {
    final ColorScheme scheme = ColorScheme.fromSeed(
      seedColor: AppColors.orange,
      brightness: Brightness.light,
    ).copyWith(
      surface: AppColors.ivory,
      primary: AppColors.orange,
      onPrimary: AppColors.ivory,
      onSurface: AppColors.ink,
    );

    const TextTheme text = TextTheme(
      displaySmall: AppTypography.displayTitle,
      titleLarge: AppTypography.titleL,
      titleMedium: AppTypography.titleM,
      bodyLarge: AppTypography.bodyInput,
      bodyMedium: AppTypography.body,
      bodySmall: AppTypography.caption,
      labelLarge: AppTypography.label,
    );

    return ThemeData(
      useMaterial3: true,
      brightness: Brightness.light,
      colorScheme: scheme,
      scaffoldBackgroundColor: AppColors.sand,
      fontFamily: AppFonts.sans,
      textTheme: text,
    );
  }
}
