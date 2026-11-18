import { StyleSheet, Text, View, type StyleProp, type TextStyle } from 'react-native';

import { parseChatMarkdown } from '@/services/chatMarkdown';
import { spacing } from '@/theme';

export function ChatMarkdown({
  text,
  style,
}: {
  text: string;
  style?: StyleProp<TextStyle>;
}) {
  const blocks = parseChatMarkdown(text);
  return (
    <View style={styles.wrap}>
      {blocks.map((block, index) => {
        if (block.kind === 'break') {
          return <View key={index} style={styles.break} />;
        }
        const content = (
          <Text style={[style, block.kind === 'heading' && styles.heading]}>
            {block.spans.map((span, spanIndex) => (
              <Text
                key={spanIndex}
                style={span.bold ? styles.bold : undefined}>
                {span.text}
              </Text>
            ))}
          </Text>
        );
        if (block.kind === 'bullet' || block.kind === 'number') {
          return (
            <View key={index} style={styles.listRow}>
              <Text style={[style, styles.marker]}>{block.marker}</Text>
              {content}
            </View>
          );
        }
        return <View key={index}>{content}</View>;
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    gap: 4,
  },
  break: {
    height: spacing.xs,
  },
  listRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 6,
  },
  marker: {
    minWidth: 14,
  },
  heading: {
    fontWeight: '700',
  },
  bold: {
    fontWeight: '700',
  },
});
