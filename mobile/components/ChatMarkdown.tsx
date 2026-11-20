import {
  ScrollView,
  StyleSheet,
  Text,
  View,
  type StyleProp,
  type TextStyle,
} from 'react-native';

import { parseChatMarkdown, type ChatMarkdownSpan } from '@/services/chatMarkdown';
import { palette, spacing } from '@/theme';

function MarkdownText({
  spans,
  style,
  boldStyle,
}: {
  spans: ChatMarkdownSpan[];
  style?: StyleProp<TextStyle>;
  boldStyle?: StyleProp<TextStyle>;
}) {
  return (
    <Text style={[style, boldStyle]}>
      {spans.map((span, index) => (
        <Text key={index} style={span.bold ? styles.bold : undefined}>
          {span.text}
        </Text>
      ))}
    </Text>
  );
}

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
        if (block.kind === 'table') {
          return (
            <ScrollView
              key={index}
              horizontal
              nestedScrollEnabled
              showsHorizontalScrollIndicator={false}
              style={styles.tableScroll}
              contentContainerStyle={styles.tableFrame}>
              <View>
                <View style={[styles.tableRow, styles.tableHeaderRow]}>
                  {block.header.map((cell, cellIndex) => (
                    <View key={cellIndex} style={styles.tableCell}>
                      <MarkdownText
                        spans={cell}
                        style={[style, styles.tableHeaderText]}
                      />
                    </View>
                  ))}
                </View>
                {block.rows.map((row, rowIndex) => (
                  <View
                    key={rowIndex}
                    style={[
                      styles.tableRow,
                      rowIndex % 2 === 1 && styles.tableRowAlt,
                    ]}>
                    {row.map((cell, cellIndex) => (
                      <View key={cellIndex} style={styles.tableCell}>
                        <MarkdownText spans={cell} style={style} />
                      </View>
                    ))}
                  </View>
                ))}
              </View>
            </ScrollView>
          );
        }
        const content = (
          <MarkdownText
            spans={block.spans}
            style={style}
            boldStyle={block.kind === 'heading' ? styles.heading : undefined}
          />
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
  tableScroll: {
    marginVertical: spacing.xs,
  },
  tableFrame: {
    paddingRight: spacing.xs,
  },
  tableRow: {
    flexDirection: 'row',
    borderBottomWidth: 1,
    borderBottomColor: palette.line,
  },
  tableRowAlt: {
    backgroundColor: palette.surface,
  },
  tableHeaderRow: {
    backgroundColor: palette.accentSoft,
  },
  tableCell: {
    width: 128,
    minHeight: 34,
    justifyContent: 'center',
    paddingHorizontal: spacing.sm,
    paddingVertical: 6,
    borderRightWidth: 1,
    borderRightColor: palette.line,
  },
  tableHeaderText: {
    fontWeight: '700',
  },
});
