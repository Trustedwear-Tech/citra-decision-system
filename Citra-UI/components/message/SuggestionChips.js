import React from 'react';
import { View, Text, TouchableOpacity, StyleSheet } from 'react-native';
import { Ionicons } from '@expo/vector-icons';

/**
 * SuggestionChips
 * ----------------
 * Renders a row of clickable chips for an LLM-emitted <clarify> block (or
 * server-side filter-mismatch suggestion). Tapping a chip submits the chip's
 * `follow_up_query` (or `value`) as the next user turn via `onPick`.
 *
 * Props:
 *   suggestions: Array<{ message, reason?, column?, options: [...], allow_freeform? }>
 *   onPick: (text: string, option: object, suggestion: object) => void
 *   theme: theme object (uses theme.primary, theme.text, theme.borderColor)
 *   disabled?: boolean — if true, chips render but do not trigger onPick
 */
const SuggestionChips = ({ suggestions, onPick, theme, disabled = false }) => {
  if (!Array.isArray(suggestions) || suggestions.length === 0) return null;

  const accent = theme?.primary || theme?.sendButton || '#2F56E9';
  const textColor = theme?.text || '#222';
  const borderColor = theme?.borderColor || 'rgba(0,0,0,0.08)';
  const subtleText = theme?.secondaryText || 'rgba(0,0,0,0.55)';
  const chipBg = theme?.cardBackground || 'rgba(47,86,233,0.06)';

  return (
    <View style={styles.wrap}>
      {suggestions.map((sug, sIdx) => {
        const opts = Array.isArray(sug?.options) ? sug.options : [];
        if (opts.length === 0) return null;
        return (
          <View key={sIdx} style={styles.block}>
            {sug.message ? (
              <Text style={[styles.prompt, { color: subtleText }]}>{sug.message}</Text>
            ) : null}
            <View style={styles.row}>
              {opts.map((opt, idx) => {
                const label = opt?.label || opt?.value || `Option ${idx + 1}`;
                const handlePress = () => {
                  if (disabled) return;
                  const text =
                    (typeof opt?.follow_up_query === 'string' && opt.follow_up_query.trim()) ||
                    (typeof opt?.value === 'string' && opt.value.trim()) ||
                    label;
                  onPick?.(text, opt, sug);
                };
                return (
                  <TouchableOpacity
                    key={opt?.id || idx}
                    onPress={handlePress}
                    activeOpacity={disabled ? 1 : 0.6}
                    style={[
                      styles.chip,
                      {
                        borderColor: accent,
                        backgroundColor: chipBg,
                        opacity: disabled ? 0.5 : 1,
                      },
                    ]}
                    accessibilityRole="button"
                    accessibilityLabel={`Suggestion: ${label}`}
                  >
                    <Ionicons
                      name="sparkles-outline"
                      size={13}
                      color={accent}
                      style={styles.chipIcon}
                    />
                    <Text
                      style={[styles.chipText, { color: accent }]}
                      numberOfLines={1}
                    >
                      {label}
                    </Text>
                    {typeof opt?.count === 'number' ? (
                      <Text style={[styles.chipCount, { color: subtleText }]}>
                        {`  · ${opt.count}`}
                      </Text>
                    ) : null}
                  </TouchableOpacity>
                );
              })}
            </View>
          </View>
        );
      })}
    </View>
  );
};

const styles = StyleSheet.create({
  wrap: {
    marginTop: 8,
    marginBottom: 4,
    width: '100%',
  },
  block: {
    marginBottom: 8,
  },
  prompt: {
    fontSize: 12,
    marginBottom: 6,
    lineHeight: 16,
  },
  row: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 6,
  },
  chip: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 16,
    borderWidth: 1,
    marginRight: 6,
    marginBottom: 6,
    maxWidth: '100%',
  },
  chipIcon: {
    marginRight: 4,
  },
  chipText: {
    fontSize: 13,
    fontWeight: '500',
  },
  chipCount: {
    fontSize: 11,
    fontWeight: '400',
  },
});

export default SuggestionChips;
