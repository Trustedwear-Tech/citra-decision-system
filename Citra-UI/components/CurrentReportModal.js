import React, { useState, useEffect } from 'react';
import {
  View,
  Text,
  ScrollView,
  TouchableOpacity,
  Modal,
  Dimensions,
  Alert,
  Share,
  Clipboard,
  Platform,
  ActivityIndicator
} from 'react-native';
import * as Print from 'expo-print';
import * as Sharing from 'expo-sharing';
import { API_CONFIG } from '../config/config';
import { styles } from '../styles';
import authService from '../services/authService';

// Import existing text formatting utilities
import { formatTitle, parseMessageContent } from '../utils/textProcessing';

const { width: screenWidth, height: screenHeight } = Dimensions.get('window');

// Web HTML Renderer for formatted content (similar to the one in App.js)
const WebHTMLRenderer = ({ content, theme, style }) => {
  // Convert LaTeX math to HTML for better rendering
  const processContent = (text) => {
    try {
      // Sanitize input to prevent rendering issues
      if (!text || typeof text !== 'string') {
        return '';
      }

      // Process markdown headers
      text = text.replace(/###\s+(.*?)(?:\n|$)/g, (_, heading) => `
        <h3 style="
          font-size: 18px;
          font-weight: bold;
          color: ${theme.text};
          margin: 16px 0 8px 0;
          padding: 0;
          line-height: 1.4;
        ">${heading}</h3>
      `);

      text = text.replace(/##\s+(.*?)(?:\n|$)/g, (_, heading) => `
        <h2 style="
          font-size: 20px;
          font-weight: bold;
          color: ${theme.text};
          margin: 20px 0 10px 0;
          padding: 0;
          line-height: 1.4;
        ">${heading}</h2>
      `);

      text = text.replace(/#\s+(.*?)(?:\n|$)/g, (_, heading) => `
        <h1 style="
          font-size: 22px;
          font-weight: bold;
          color: ${theme.text};
          margin: 24px 0 12px 0;
          padding: 0;
          line-height: 1.4;
        ">${heading}</h1>
      `);

      return text
        // Handle bold **text**
        .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
        // Handle italic *text*
        .replace(/\*(.*?)\*/g, '<em>$1</em>')
        // Handle inline code `code`
        .replace(/`([^`]+)`/g, `<code style="
          background-color: ${theme.isDark ? '#404040' : '#f3f4f4'};
          color: ${theme.isDark ? '#f8f8f2' : '#e83e8c'};
          padding: 2px 4px;
          border-radius: 3px;
          font-family: 'Courier New', monospace;
          font-size: 14px;
        ">$1</code>`)
        // Handle bullet points
        .replace(/^[\s]*[-*+]\s+(.+)$/gm, `<li style="margin: 4px 0; color: ${theme.text};">$1</li>`)
        // Handle numbered lists
        .replace(/^[\s]*\d+\.\s+(.+)$/gm, `<li style="margin: 4px 0; color: ${theme.text};">$1</li>`)
        // Convert line breaks to <br>
        .replace(/\n/g, '<br>');
    } catch (error) {
      console.error('Error processing content for WebHTMLRenderer:', error);
      // Return sanitized plain text as fallback
      return (text || '').replace(/\n/g, '<br>');
    }
  };

  const safeGetHtmlContent = () => {
    try {
      return processContent(content);
    } catch (error) {
      console.error('Error generating HTML content:', error);
      // Return plain text as fallback
      return (content || '').replace(/\n/g, '<br>');
    }
  };

  const htmlContent = safeGetHtmlContent();

  if (Platform.OS === 'web') {
    try {
      return (
        <div
          style={{
            color: theme.text,
            fontSize: 16,
            fontWeight: '400',
            lineHeight: 1.6,
            fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
          }}
          dangerouslySetInnerHTML={{ __html: htmlContent }}
        />
      );
    } catch (error) {
      console.error('Error rendering HTML content:', error);
      // Fallback to plain text rendering
      return (
        <div style={{ color: theme.text }}>
          {content || ''}
        </div>
      );
    }
  } else {
    // Fallback for non-web platforms - use the existing formatTitle function
    const formattedText = formatTitle(content, theme);
    return <Text style={style}>{formattedText}</Text>;
  }
};

// Formatted Message Content component for the modal
const FormattedModalContent = ({ content, theme }) => {
  // Safety check for undefined content and convert to string if needed
  if (content === null || content === undefined) {
    return null;
  }

  // Convert content to string if it's not already
  const contentStr = typeof content === 'string' ? content : String(content);

  if (Platform.OS === 'web') {
    return (
      <WebHTMLRenderer
        content={contentStr}
        theme={theme}
      />
    );
  } else {
    // Mobile: Use the existing formatTitle function
    const formattedText = formatTitle(contentStr, theme);

    if (typeof formattedText === 'string') {
      return <Text style={{ color: theme.text, fontSize: 16, lineHeight: 24 }}>{formattedText}</Text>;
    }

    return <Text style={{ color: theme.text, fontSize: 16, lineHeight: 24 }}>{formattedText}</Text>;
  }
};

const CurrentReportModal = ({
  visible,
  onClose,
  reportId,
  deviceId,
  theme
}) => {
  const [reportData, setReportData] = useState(null);
  const [loading, setLoading] = useState(false);

  // Load report data from MongoDB when modal opens
  useEffect(() => {
    if (visible && reportId) {
      loadReportFromDatabase();
    }
  }, [visible, reportId]);

  const loadReportFromDatabase = async () => {
    try {
      setLoading(true);
      console.log('🔬 CurrentReportModal - Loading report from database:', reportId);

      const baseUrl = API_CONFIG.CITRA_SERVICE_URL;

      // Use authService for authenticated request
      const response = await authService.authenticatedFetch(`${baseUrl}/deep-research-report/${reportId}`, {
        method: 'GET',
        headers: {
          'Content-Type': 'application/json',
        },
      });

      if (response.ok) {
        const data = await response.json();
        console.log('🔬 CurrentReportModal - Loaded report data:', data);
        setReportData(data);
      } else {
        console.error('Failed to load report from database:', response.status);
        Alert.alert('Error', 'Failed to load report details');
      }
    } catch (error) {
      console.error('Error loading report from database:', error);
      Alert.alert('Error', 'Failed to load report details');
    } finally {
      setLoading(false);
    }
  };

  // Debug logging
  // console.log('🔬 CurrentReportModal - visible:', visible);
  // console.log('🔬 CurrentReportModal - reportId:', reportId);
  // console.log('🔬 CurrentReportModal - Received props:', {
  //   reportId,
  //   visible,
  //   deviceId
  // });
  // console.log('🔬 CurrentReportModal - reportData:', reportData);

  // Helper function to format duration
  const formatDuration = (seconds) => {
    if (!seconds) return 'N/A';
    if (seconds < 60) return `${Math.round(seconds)}s`;
    const minutes = Math.floor(seconds / 60);
    const remainingSeconds = Math.round(seconds % 60);
    return `${minutes}m ${remainingSeconds}s`;
  };

  // Helper function to format date
  const formatDate = (dateString) => {
    if (!dateString) return 'N/A';
    try {
      return new Date(dateString).toLocaleDateString('en-US', {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
      });
    } catch {
      return 'N/A';
    }
  };

  if (!visible) {
    return null;
  }

  if (loading) {
    return (
      <Modal
        animationType="slide"
        transparent={true}
        visible={visible}
        onRequestClose={onClose}
      >
        <View style={[
          styles.modalContainer,
          { backgroundColor: 'rgba(0,0,0,0.5)' }
        ]}>
          <View style={[
            styles.modalContent,
            {
              backgroundColor: theme.surface,
              justifyContent: 'center',
              alignItems: 'center',
              height: 200
            }
          ]}>
            <ActivityIndicator size="large" color={theme.primary} />
            <Text style={{ color: theme.text, marginTop: 20, fontSize: 16 }}>
              Loading report...
            </Text>
          </View>
        </View>
      </Modal>
    );
  }

  if (!reportData) {
    return null;
  }

  // Safe data extraction from MongoDB report data (using correct structure)
  const reportContent = reportData.report_content || {};
  const researchMetadata = reportContent.research_metadata || reportData.researchMetadata || {};
  const researchSummary = reportContent.research_summary || {};
  const query = reportData.original_query || reportData.query || reportData.originalQuery || 'No query provided';
  const findings = Array.isArray(reportData.findings) ? reportData.findings : [];
  const citations = Array.isArray(reportData.citations) ? reportData.citations : [];

  // Get the research answer from correct MongoDB structure
  const researchAnswer = reportContent.final_answer ||           // ← Primary location in MongoDB
    reportData.final_answer ||              // ← Fallback for legacy data
    reportData.answer ||
    reportData.researchAnswer ||
    researchMetadata.final_answer ||
    'Research answer not available';

  // Helper function to safely render text content
  const safeTextRender = (content, fallback = 'No content available') => {
    if (!content) return fallback;
    if (typeof content === 'string') return content;
    if (typeof content === 'object') {
      return content.title || content.content || content.summary || content.text || JSON.stringify(content);
    }
    return String(content);
  };

  // Generate report content for sharing/copying
  const generateReportContent = () => {
    let reportContent = `🔬 Deep Research Report\n\n`;

    // Query
    reportContent += `📝 Research Query:\n${safeTextRender(query)}\n\n`;

    // Answer
    if (researchAnswer && researchAnswer !== 'Research answer not available') {
      reportContent += `💡 Research Answer:\n${safeTextRender(researchAnswer)}\n\n`;
    }

    // Quick Stats (using correct MongoDB structure)
    reportContent += `📊 Summary:\n`;
    reportContent += `• Findings: ${findings?.length || 0}\n`;
    reportContent += `• Sources: ${citations?.length || 0}\n`;
    reportContent += `• Status: ${researchSummary?.completed_at || reportData?.status || 'In Progress'}\n`;
    reportContent += `• Duration: ${researchSummary?.duration_seconds ? `${Math.round(researchSummary.duration_seconds)}s` : 'N/A'}\n\n`;

    // Findings
    if (findings && findings.length > 0) {
      reportContent += `🔍 Key Findings:\n`;
      findings.slice(0, 10).forEach((finding, index) => {
        reportContent += `${index + 1}. ${safeTextRender(finding)}\n`;
      });
      reportContent += '\n';
    }

    // Citations
    if (citations && citations.length > 0) {
      reportContent += `📚 Citations:\n`;
      citations.slice(0, 15).forEach((citation, index) => {
        reportContent += `${index + 1}. ${safeTextRender(citation)}`;
        if (citation && typeof citation === 'object' && citation.source_file) {
          reportContent += ` (${safeTextRender(citation.source_file)})`;
        }
        reportContent += '\n';
      });
    }

    reportContent += `\n---\nGenerated by Citra AI • ${new Date().toLocaleDateString()}`;

    return reportContent;
  };

  // Generate HTML content for PDF
  const generateHTMLContent = () => {
    const styles = `
      <style>
        body {
          font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
          line-height: 1.6;
          color: #333;
          max-width: 800px;
          margin: 0 auto;
          padding: 20px;
        }
        .header {
          text-align: center;
          border-bottom: 2px solid #007AFF;
          padding-bottom: 20px;
          margin-bottom: 30px;
        }
        .header h1 {
          color: #007AFF;
          margin: 0;
          font-size: 28px;
        }
        .section {
          margin-bottom: 30px;
        }
        .section-title {
          font-size: 18px;
          font-weight: bold;
          color: #007AFF;
          margin-bottom: 15px;
          padding-bottom: 5px;
          border-bottom: 1px solid #eee;
        }
        .query-box {
          background-color: #f8f9fa;
          padding: 15px;
          border-radius: 8px;
          border-left: 4px solid #007AFF;
          font-style: italic;
        }
        .answer-box {
          background-color: #e8f4ff;
          padding: 20px;
          border-radius: 8px;
          border: 1px solid #007AFF;
        }
        .stats-grid {
          display: flex;
          gap: 20px;
          margin: 20px 0;
        }
        .stat-item {
          flex: 1;
          text-align: center;
          background-color: #f8f9fa;
          padding: 15px;
          border-radius: 8px;
        }
        .stat-number {
          font-size: 24px;
          font-weight: bold;
          color: #007AFF;
          display: block;
        }
        .stat-label {
          font-size: 12px;
          color: #666;
        }
        .finding-item {
          background-color: #f8f9fa;
          padding: 12px;
          margin-bottom: 8px;
          border-radius: 6px;
          border-left: 3px solid #007AFF;
        }
        .citation-item {
          background-color: #f8f9fa;
          padding: 12px;
          margin-bottom: 12px;
          border-radius: 6px;
          border-left: 3px solid #28a745;
        }
        .citation-source {
          font-size: 11px;
          color: #666;
          font-style: italic;
          margin-top: 4px;
        }
        .footer {
          text-align: center;
          margin-top: 40px;
          padding-top: 20px;
          border-top: 1px solid #eee;
          color: #666;
          font-size: 12px;
        }
      </style>
    `;

    let htmlContent = `
      <!DOCTYPE html>
      <html>
      <head>
        <meta charset="utf-8">
        <title>Deep Research Report</title>
        ${styles}
      </head>
      <body>
        <div class="header">
          <h1>🔬 Deep Research Report</h1>
        </div>
        
        <div class="section">
          <div class="section-title">📝 Research Query</div>
          <div class="query-box">${safeTextRender(query)}</div>
        </div>
    `;

    // Add answer section
    if (researchAnswer && researchAnswer !== 'Research answer not available') {
      htmlContent += `
        <div class="section">
          <div class="section-title">💡 Research Answer</div>
          <div class="answer-box">${safeTextRender(researchAnswer).replace(/\n/g, '<br>')}</div>
        </div>
      `;
    }

    // Add stats
    htmlContent += `
      <div class="section">
        <div class="stats-grid">
          <div class="stat-item">
            <span class="stat-number">${findings?.length || 0}</span>
            <span class="stat-label">Findings</span>
          </div>
          <div class="stat-item">
            <span class="stat-number">${citations?.length || 0}</span>
            <span class="stat-label">Sources</span>
          </div>
          <div class="stat-item">
            <span class="stat-number">✓</span>
            <span class="stat-label">${researchMetadata?.completed_at ? 'Complete' : 'In Progress'}</span>
          </div>
        </div>
      </div>
    `;

    // Add findings
    if (findings && findings.length > 0) {
      htmlContent += `
        <div class="section">
          <div class="section-title">🔍 Key Findings (${findings.length})</div>
      `;
      findings.slice(0, 10).forEach((finding, index) => {
        htmlContent += `
          <div class="finding-item">
            <strong>${index + 1}.</strong> ${safeTextRender(finding)}
          </div>
        `;
      });
      htmlContent += `</div>`;
    }

    // Add citations
    if (citations && citations.length > 0) {
      htmlContent += `
        <div class="section">
          <div class="section-title">📚 Citations (${citations.length})</div>
      `;
      citations.slice(0, 15).forEach((citation, index) => {
        const title = citation.title || citation.document_title || citation.source_file || `Citation ${index + 1}`;
        htmlContent += `
          <div class="citation-item">
            <strong>${index + 1}.</strong> ${title}
        `;
        const sourceInfo = citation.detailed_source || citation.source_file || citation.filename;
        if (sourceInfo) {
          htmlContent += `
            <div class="citation-source">
              Source: ${sourceInfo}${citation.page_numbers && citation.page_numbers.length > 0 ? ` (Page ${citation.page_numbers.join(', ')})` : ''}${citation.type ? ` • ${citation.type}` : ''}
            </div>
          `;
        }
        htmlContent += `</div>`;
      });
      htmlContent += `</div>`;
    }

    htmlContent += `
        <div class="footer">
          Generated by Citra AI • ${new Date().toLocaleDateString()}
        </div>
      </body>
      </html>
    `;

    return htmlContent;
  };

  // Copy to clipboard function
  const handleCopyToClipboard = async () => {
    try {
      const reportContent = generateReportContent();

      if (Platform.OS === 'web') {
        // Web clipboard API
        if (navigator.clipboard) {
          await navigator.clipboard.writeText(reportContent);
        } else {
          // Fallback for older browsers
          const textArea = document.createElement('textarea');
          textArea.value = reportContent;
          document.body.appendChild(textArea);
          textArea.select();
          document.execCommand('copy');
          document.body.removeChild(textArea);
        }
      } else {
        // React Native clipboard
        Clipboard.setString(reportContent);
      }

      Alert.alert('Success', 'Report copied to clipboard!');
    } catch (error) {
      console.error('Error copying to clipboard:', error);
      Alert.alert('Error', 'Failed to copy report to clipboard');
    }
  };

  // Share function
  const handleShare = async () => {
    try {
      const reportContent = generateReportContent();

      if (Platform.OS === 'web') {
        // Web Share API or fallback
        if (navigator.share) {
          await navigator.share({
            title: 'Deep Research Report',
            text: reportContent
          });
        } else {
          // Fallback: copy to clipboard and notify user
          await handleCopyToClipboard();
        }
      } else {
        // React Native Share
        const result = await Share.share({
          message: reportContent,
          title: 'Deep Research Report'
        });

        if (result.action === Share.sharedAction) {
          Alert.alert('Success', 'Report shared successfully!');
        }
      }
    } catch (error) {
      console.error('Error sharing report:', error);
      Alert.alert('Error', 'Failed to share report');
    }
  };

  // PDF download function
  const handleDownloadPDF = async () => {
    try {
      const htmlContent = generateHTMLContent();

      if (Platform.OS === 'web') {
        // Web: Create and download PDF blob
        const printWindow = window.open('', '_blank');
        printWindow.document.write(htmlContent);
        printWindow.document.close();

        // Trigger print dialog
        printWindow.focus();
        printWindow.print();

        // For download, we would need a PDF generation library like jsPDF
        // For now, we'll show the print dialog

      } else {
        // React Native: Use expo-print
        const { uri } = await Print.printToFileAsync({
          html: htmlContent,
          base64: false
        });

        // Share the PDF file
        if (await Sharing.isAvailableAsync()) {
          await Sharing.shareAsync(uri, {
            mimeType: 'application/pdf',
            dialogTitle: 'Save Deep Research Report PDF'
          });
        }

        Alert.alert('Success', 'PDF generated and ready to share!');
      }
    } catch (error) {
      console.error('Error generating PDF:', error);
      Alert.alert('Error', 'Failed to generate PDF');
    }
  };

  return (
    <Modal
      visible={visible}
      animationType="slide"
      onRequestClose={onClose}
      presentationStyle="formSheet"
    >
      <View style={[reportModalStyles.container, { backgroundColor: theme.background }]}>
        <View style={[reportModalStyles.header, { borderBottomColor: theme.borderColor }]}>
          <Text style={[reportModalStyles.title, { color: theme.text }]}>
            🔬 Deep Research Report
          </Text>

          {/* Action Buttons */}
          <View style={reportModalStyles.actionButtons}>
            <TouchableOpacity
              style={[reportModalStyles.actionButton, { backgroundColor: theme.inputBackground, borderColor: theme.borderColor }]}
              onPress={handleCopyToClipboard}
            >
              <Text style={[reportModalStyles.actionButtonText, { color: theme.text }]}>📋</Text>
            </TouchableOpacity>

            <TouchableOpacity
              style={[reportModalStyles.actionButton, { backgroundColor: theme.inputBackground, borderColor: theme.borderColor }]}
              onPress={handleShare}
            >
              <Text style={[reportModalStyles.actionButtonText, { color: theme.text }]}>📤</Text>
            </TouchableOpacity>

            <TouchableOpacity
              style={[reportModalStyles.actionButton, { backgroundColor: theme.inputBackground, borderColor: theme.borderColor }]}
              onPress={handleDownloadPDF}
            >
              <Text style={[reportModalStyles.actionButtonText, { color: theme.text }]}>📄</Text>
            </TouchableOpacity>
          </View>

          <TouchableOpacity
            style={[reportModalStyles.closeButton, { backgroundColor: theme.borderColor }]}
            onPress={onClose}
          >
            <Text style={[reportModalStyles.closeButtonText, { color: theme.text }]}>✕</Text>
          </TouchableOpacity>
        </View>

        <ScrollView
          style={reportModalStyles.content}
          showsVerticalScrollIndicator={true}
          contentContainerStyle={reportModalStyles.contentContainer}
        >
          {/* Original Query Section */}
          <View style={[reportModalStyles.section, { borderColor: theme.borderColor }]}>
            <Text style={[reportModalStyles.sectionTitle, { color: theme.text }]}>
              📝 Research Query
            </Text>
            <Text style={[reportModalStyles.query, { color: theme.text, backgroundColor: theme.inputBackground }]}>
              {safeTextRender(query)}
            </Text>
          </View>

          {/* Research Answer Section - Main Focus */}
          {researchAnswer && (
            <View style={[reportModalStyles.section, { borderColor: theme.borderColor }]}>
              <Text style={[reportModalStyles.sectionTitle, { color: theme.text }]}>
                💡 Research Answer
              </Text>
              <View style={[reportModalStyles.answer, {
                backgroundColor: theme.inputBackground,
                borderColor: theme.borderColor
              }]}>
                <FormattedModalContent
                  content={safeTextRender(researchAnswer)}
                  theme={theme}
                />
              </View>
            </View>
          )}

          {/* Quick Summary Stats */}
          <View style={[reportModalStyles.section, { borderColor: theme.borderColor, paddingVertical: 12 }]}>
            <View style={[reportModalStyles.quickStats, { backgroundColor: theme.inputBackground }]}>
              <View style={reportModalStyles.statItem}>
                <Text style={[reportModalStyles.statNumber, { color: theme.text }]}>{findings?.length || 0}</Text>
                <Text style={[reportModalStyles.statLabel, { color: theme.placeholderText }]}>Findings</Text>
              </View>
              <View style={reportModalStyles.statItem}>
                <Text style={[reportModalStyles.statNumber, { color: theme.text }]}>{citations?.length || 0}</Text>
                <Text style={[reportModalStyles.statLabel, { color: theme.placeholderText }]}>Sources</Text>
              </View>
              {(researchSummary?.completed_at || reportData?.status === 'completed') && (
                <View style={reportModalStyles.statItem}>
                  <Text style={[reportModalStyles.statNumber, { color: theme.text }]}>✓</Text>
                  <Text style={[reportModalStyles.statLabel, { color: theme.placeholderText }]}>Complete</Text>
                </View>
              )}
              {researchSummary?.duration_seconds && (
                <View style={reportModalStyles.statItem}>
                  <Text style={[reportModalStyles.statNumber, { color: theme.text }]}>
                    {Math.round(researchSummary.duration_seconds)}s
                  </Text>
                  <Text style={[reportModalStyles.statLabel, { color: theme.placeholderText }]}>Duration</Text>
                </View>
              )}
            </View>
          </View>

          {/* Key Findings Section */}
          {findings && findings.length > 0 && (
            <View style={[reportModalStyles.section, { borderColor: theme.borderColor }]}>
              <Text style={[reportModalStyles.sectionTitle, { color: theme.text }]}>
                🔍 Key Findings ({findings.length})
              </Text>
              {findings.slice(0, 10).map((finding, index) => (
                <View key={index} style={[reportModalStyles.finding, {
                  backgroundColor: theme.inputBackground,
                  borderColor: theme.borderColor
                }]}>
                  <Text style={[reportModalStyles.findingNumber, { color: theme.placeholderText }]}>
                    {index + 1}.
                  </Text>
                  <Text style={[reportModalStyles.findingText, { color: theme.text }]}>
                    {safeTextRender(finding)}
                  </Text>
                </View>
              ))}
            </View>
          )}

          {/* Citations Section */}
          {citations && citations.length > 0 && (
            <View style={[reportModalStyles.section, { borderColor: theme.borderColor }]}>
              <Text style={[reportModalStyles.sectionTitle, { color: theme.text }]}>
                📚 Citations ({citations.length})
              </Text>
              {citations.slice(0, 15).map((citation, index) => (
                <View key={index} style={[reportModalStyles.citation, {
                  backgroundColor: theme.inputBackground,
                  borderColor: theme.borderColor
                }]}>
                  <Text style={[reportModalStyles.citationTitle, { color: theme.text }]}>
                    {citation.title || citation.document_title || citation.source_file || `Citation ${index + 1}`}
                  </Text>
                  {(citation.detailed_source || citation.source_file || citation.filename) && (
                    <Text style={[reportModalStyles.citationSource, { color: theme.placeholderText }]}>
                      {citation.detailed_source || citation.source_file || citation.filename}
                      {citation.page_numbers && citation.page_numbers.length > 0 && ` (Page ${citation.page_numbers.join(', ')})`}
                      {citation.type && ` • ${citation.type}`}
                    </Text>
                  )}
                  {(citation.excerpt || citation.context || citation.text) && (
                    <Text style={[reportModalStyles.citationExcerpt, { color: theme.text }]}>
                      "{(citation.excerpt || citation.context || citation.text).substring(0, 200)}..."
                    </Text>
                  )}
                </View>
              ))}
            </View>
          )}
        </ScrollView>
      </View>
    </Modal>
  );
};

const reportModalStyles = {
  container: {
    flex: 1,
    paddingTop: 50, // Account for status bar
  },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: 20,
    borderBottomWidth: 1,
    paddingTop: 10,
  },
  title: {
    fontSize: 20,
    fontWeight: 'bold',
    flex: 1,
  },
  actionButtons: {
    flexDirection: 'row',
    alignItems: 'center',
    marginRight: 12,
    gap: 8,
  },
  actionButton: {
    width: 36,
    height: 36,
    borderRadius: 18,
    justifyContent: 'center',
    alignItems: 'center',
    borderWidth: 1,
  },
  actionButtonText: {
    fontSize: 16,
  },
  closeButton: {
    width: 32,
    height: 32,
    borderRadius: 16,
    justifyContent: 'center',
    alignItems: 'center',
  },
  closeButtonText: {
    fontSize: 16,
    fontWeight: 'bold',
  },
  content: {
    flex: 1,
  },
  contentContainer: {
    padding: 20,
    paddingBottom: 40, // Extra padding at bottom
  },
  section: {
    marginBottom: 24,
    borderBottomWidth: 1,
    paddingBottom: 16,
  },
  sectionTitle: {
    fontSize: 16,
    fontWeight: '600',
    marginBottom: 12,
  },
  query: {
    fontSize: 14,
    lineHeight: 20,
    padding: 12,
    borderRadius: 8,
    fontStyle: 'italic',
  },
  summaryGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
    justifyContent: 'space-between',
  },
  summaryItem: {
    flex: 1,
    minWidth: '48%',
    padding: 12,
    borderRadius: 8,
    marginBottom: 8,
  },
  summaryLabel: {
    fontSize: 12,
    fontWeight: '500',
    marginBottom: 4,
  },
  summaryValue: {
    fontSize: 14,
    fontWeight: '600',
  },
  answer: {
    padding: 20,
    borderRadius: 12,
    borderWidth: 1,
    marginBottom: 8,
  },
  quickStats: {
    flexDirection: 'row',
    justifyContent: 'space-around',
    padding: 16,
    borderRadius: 8,
  },
  statItem: {
    alignItems: 'center',
  },
  statNumber: {
    fontSize: 20,
    fontWeight: 'bold',
    marginBottom: 4,
  },
  statLabel: {
    fontSize: 12,
    textAlign: 'center',
  },
  finding: {
    flexDirection: 'row',
    padding: 12,
    borderRadius: 8,
    marginBottom: 8,
    borderWidth: 1,
  },
  findingNumber: {
    fontSize: 12,
    fontWeight: '600',
    marginRight: 8,
    marginTop: 2,
    width: 20,
  },
  findingText: {
    fontSize: 13,
    lineHeight: 18,
    flex: 1,
  },
  citation: {
    padding: 12,
    borderRadius: 8,
    marginBottom: 12,
    borderWidth: 1,
  },
  citationTitle: {
    fontSize: 13,
    fontWeight: '600',
    marginBottom: 4,
  },
  citationSource: {
    fontSize: 11,
    marginBottom: 6,
    fontStyle: 'italic',
  },
  citationExcerpt: {
    fontSize: 12,
    lineHeight: 16,
    fontStyle: 'italic',
    opacity: 0.8,
  },
};

export default CurrentReportModal;
