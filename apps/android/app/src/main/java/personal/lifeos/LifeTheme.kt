package personal.lifeos

import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp

private val LifeColors = darkColorScheme(
    primary = Color(0xFFA8C5A6), onPrimary = Color(0xFF172218),
    primaryContainer = Color(0xFF2B3B2E), onPrimaryContainer = Color(0xFFD4E6CE),
    secondary = Color(0xFFBBCBB5), onSecondary = Color(0xFF223022),
    secondaryContainer = Color(0xFF2A352C), onSecondaryContainer = Color(0xFFD4DFCE),
    background = Color(0xFF101311), onBackground = Color(0xFFE8EBE7),
    surface = Color(0xFF191E1B), onSurface = Color(0xFFE8EBE7),
    surfaceVariant = Color(0xFF29322C), onSurfaceVariant = Color(0xFFB1BFB3),
    outline = Color(0xFF697D6B), outlineVariant = Color(0xFF344238),
    error = Color(0xFFF0B9A3), onError = Color(0xFF43271E),
    errorContainer = Color(0xFF422C24), onErrorContainer = Color(0xFFF3C9B7),
)

private val LifeFont = FontFamily(Font(R.font.inter))
private val LifeTypography = Typography().let { base -> base.copy(
    displayLarge = base.displayLarge.copy(fontFamily = LifeFont),
    displayMedium = base.displayMedium.copy(fontFamily = LifeFont),
    displaySmall = base.displaySmall.copy(fontFamily = LifeFont),
    headlineLarge = base.headlineLarge.copy(fontFamily = LifeFont),
    headlineMedium = base.headlineMedium.copy(fontFamily = LifeFont),
    headlineSmall = base.headlineSmall.copy(fontFamily = LifeFont),
    titleLarge = base.titleLarge.copy(fontFamily = LifeFont),
    titleMedium = base.titleMedium.copy(fontFamily = LifeFont),
    titleSmall = base.titleSmall.copy(fontFamily = LifeFont),
    bodyLarge = base.bodyLarge.copy(fontFamily = LifeFont),
    bodyMedium = base.bodyMedium.copy(fontFamily = LifeFont),
    bodySmall = base.bodySmall.copy(fontFamily = LifeFont),
    labelLarge = base.labelLarge.copy(fontFamily = LifeFont),
    labelMedium = base.labelMedium.copy(fontFamily = LifeFont),
    labelSmall = base.labelSmall.copy(fontFamily = LifeFont),
) }

@Composable
fun LifeTheme(content: @Composable () -> Unit) {
    MaterialTheme(colorScheme = LifeColors, typography = LifeTypography, shapes = Shapes(
        extraSmall = RoundedCornerShape(6.dp), small = RoundedCornerShape(10.dp),
        medium = RoundedCornerShape(18.dp), large = RoundedCornerShape(24.dp),
        extraLarge = RoundedCornerShape(28.dp),
    ), content = content)
}
