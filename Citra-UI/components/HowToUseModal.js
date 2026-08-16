import React, { useState, useEffect } from 'react';
import {
  View,
  Text,
  Modal,
  TouchableOpacity,
  ScrollView,
  StyleSheet,
  Animated,
  Platform,
  Dimensions,
  SafeAreaView,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '../hooks/useModernTheme';

const { width, height } = Dimensions.get('window');

// --- WIKI DATA STRUCTURE (The Script) ---
const WIKI_CONTENT = [
  {
    id: 'intro',
    title: 'Welcome to Citra AI',
    icon: 'sparkles',
    color: '#6366f1',
    content: [
      { type: 'header', text: 'The Future of Content Design' },
      { type: 'text', text: 'Citra AI is not just a tool; it is your intelligent partner for content creation. We believe that your data holds the answers, and our job is to help you visualize them.' },
      { type: 'text', text: 'Our core philosophy is simple: Inputs → Intelligence → Outputs.' },
      { type: 'quote', text: 'Stop manually moving data to slides. Connect your PDFs, Excel sheets, and Notes to the Data Store, and watch your Living Deck update instantly.' },
    ]
  },
  {
    id: 'getting-started',
    title: 'Getting Started',
    icon: 'rocket',
    color: '#10b981',
    content: [
      { type: 'header', text: 'Quick Start Guide' },
      { type: 'text', text: 'You can be up and running in less than 30 seconds. Here is how:' },
      { type: 'list', items: ['Create a Team to collaborate with others.', 'Create a Data Store to organize your project files.', 'Upload a document (PDF, Excel, Word).', 'Ask a question or generate a presentation!'] },
      { type: 'tip', text: 'You receive a Welcome Bonus of free tokens just for signing up!' }
    ]
  },
  {
    id: 'vaults',
    title: 'Teams & Data Stores',
    icon: 'cube',
    color: '#8b5cf6',
    content: [
      { type: 'header', text: 'Understanding Data Stores' },
      { type: 'text', text: 'Think of a Data Store as a secure container for a specific project. It isolates your data so that "Marketing Plan" documents do not get mixed up with "Engineering Specs".' },
      { type: 'header', text: 'File & Data Support' },
      { type: 'text', text: 'You can upload almost anything to a Data Store:' },
      { type: 'list', items: ['PDF Documents', 'Excel Spreadsheets & CSVs', 'Word Documents', 'PowerPoint Presentations', 'Images & Scans (OCR included)', 'Audio/Video Meeting Recordings'] },
      { type: 'warning', text: 'Always select the correct Data Store before uploading sensitive files to ensure they stay organized.' }
    ]
  },
  {
    id: 'productivity',
    title: 'Productivity Suite',
    icon: 'briefcase',
    color: '#F59E0B',
    content: [
      { type: 'header', text: 'Presentation' },
      { type: 'text', text: 'Create "Living Decks" that update when your data changes. Use commands like "Focus on Slide 5" to edit with AI.' },
      { type: 'header', text: 'Report' },
      { type: 'text', text: 'Turn 100-page PDFs into concise, executive summaries or deep-dive reports with citations.' },
      { type: 'header', text: 'Printable Report A4' },
      { type: 'text', text: 'Generate beautiful A4-size documents like quarterly reports, resumes, brochures, and newsletters using AI.' },
    ]
  },
  {
    id: 'research',
    title: 'Research Tools',
    icon: 'flask',
    color: '#3B82F6',
    content: [
      { type: 'header', text: 'Chat & Query' },
      { type: 'text', text: 'Chat directly with your data. Ask "What is our Q3 strategy?" and get answers cited from your specific PDF files.' },
    ]
  },
  {
    id: 'meetings',
    title: 'Meetings & Audio',
    icon: 'mic',
    color: '#EC4899',
    content: [
      { type: 'header', text: 'Intelligent Meetings' },
      { type: 'text', text: 'Record audio or video meetings directly into a Data Store. Citra AI will transcribe them, summarize key points, and extract action items.' },
      { type: 'tip', text: 'Meeting transcripts become part of your Data Store\'s intelligence, so you can query them later!' }
    ]
  },
  // ── Agent Builder / Workflow Engine Wiki ──────────────────────────
  {
    id: 'agent-builder-intro',
    title: 'Agent Builder — Getting Started',
    icon: 'git-network-outline',
    color: '#6366f1',
    content: [
      { type: 'header', text: 'What is the Agent Builder?' },
      { type: 'text', text: 'The Agent Builder is a visual drag-and-drop workflow engine. You connect nodes on a canvas to build automated data pipelines — no coding required (though you can add code if you want).' },
      { type: 'text', text: 'Think of it as a flowchart that actually runs. Each node performs a specific task: fetch data, process it with AI, apply logic, and send results somewhere.' },
      { type: 'quote', text: 'Trigger → Source → Process → Output. Every workflow follows this pattern.' },
      { type: 'header', text: 'Core Concepts' },
      { type: 'list', items: [
        'Nodes — Individual building blocks. Each node has a type (source, processor, output, etc.) and configuration fields you fill in.',
        'Edges — The arrows connecting nodes. Data flows along edges from one node to the next.',
        'Triggers — How a workflow starts: manually, on a schedule (cron), or via an external webhook.',
        'Environments — Every workflow has "Test" and "Production" modes with separate connection credentials.',
      ]},
      { type: 'header', text: 'Your First Workflow' },
      { type: 'list', items: [
        'Click "New Workflow" to open the canvas.',
        'Toggle the node palette (grid icon) on the left.',
        'Drag a Trigger node onto the canvas (e.g. Manual Trigger).',
        'Drag a Source or Processor node and connect them by dragging from output handle to input handle.',
        'Add an Output node (Email, Webhook, Export, etc.).',
        'Configure each node by clicking it — the right panel shows its settings.',
        'Click "Save", then "Run" to execute!',
      ]},
      { type: 'tip', text: 'Use the Templates gallery for pre-built workflows you can customize.' },
    ]
  },
  {
    id: 'agent-builder-nodes',
    title: 'Node Types & Categories',
    icon: 'cube-outline',
    color: '#8b5cf6',
    content: [
      { type: 'header', text: 'Triggers (How Workflows Start)' },
      { type: 'list', items: [
        '▶️ Manual Trigger — Run on demand by clicking "Run".',
        '🚀 Start Node — Entry point with a typed input schema. Define named parameters (text, number, boolean) that must be provided at execution time.',
        '⏰ Scheduled Trigger — Runs automatically on a cron schedule (e.g. every day at 9 AM).',
        '🔗 Webhook Trigger — Starts when an external system sends a POST request to a unique URL. Deploy the workflow to get the webhook URL.',
      ]},
      { type: 'header', text: 'Data Sources (Where Data Comes From)' },
      { type: 'list', items: [
        '🗄️ SQL Database — Query SQL Server, PostgreSQL, or MySQL. Write a SELECT query and get rows back.',
        '🍃 NoSQL Database — Query MongoDB, DocumentDB, or CosmosDB with a JSON filter.',
        '📊 CSV / Excel File — Read CSV or XLSX files from a URL or uploaded file.',
        '🌐 API Source — Make HTTP GET/POST requests to any REST API. Configure URL, headers, and body.',
        '☁️ S3 Bucket — Read files from an AWS S3 bucket.',
        '📁 SFTP / FTP File — Read files from a remote SFTP, FTP, or FTPS file server. Supports CSV, Excel, JSON, text, and binary files.',
      ]},
      { type: 'header', text: 'AI Agent' },
      { type: 'list', items: [
        '🤖 AI Agent — An autonomous agent that can reason, plan, and call tools (web search, code execution, file reading). Give it a goal and let it figure out the steps.',
      ]},
      { type: 'header', text: 'Processors (Transform & Analyze)' },
      { type: 'list', items: [
        '🤖 LLM Processor — Send data to a large language model with a custom prompt. Use {{data}} in your prompt to inject the input. Supports JSON and text output.',
        '📏 Rules Engine — Apply if/then business rules to each record.',
        '🔄 Data Transform — Reshape data: filter rows, select/rename columns, sort, aggregate, or add computed columns.',
        '🏷️ Classifier — Categorize records into labels using AI.',
        '🔍 Extractor — Pull specific fields (names, dates, amounts) from unstructured text using AI.',
        '📝 Summarizer — Condense long text into concise summaries.',
        '✅ Validator — Check data against a schema and flag invalid records.',
        '🔁 Deduplicator — Remove duplicate records based on key fields.',
        '🔗 Merge Data — Join two datasets together on matching keys.',
        '🐍 Python Code — Write custom Python code. Your input arrives as the "data" variable. Assign your output to "result". Only safe built-in functions are available (no imports).',
      ]},
      { type: 'header', text: 'Logic & Flow Control' },
      { type: 'list', items: [
        '🔀 Condition (If/Else) — Branch into two paths based on a condition. The TRUE path and FALSE path each connect to different downstream nodes.',
        '🔀 Switch Router — Branch into multiple paths (3+). Each output maps to a route index.',
        '🔁 Loop (For Each) — Iterate over each record and process it individually.',
        '⑃ Parallel Split — Split execution into parallel paths that run simultaneously.',
        '⑂ Merge Wait — Wait for all parallel branches to complete before continuing.',
        '👤 Human Approval — Pause the workflow and wait for a human to approve or reject. You will receive an email notification.',
        '⏱️ Delay — Wait a specified number of seconds before continuing.',
        '📝 Set Variable — Set or update workflow variables for downstream nodes. Define name/value pairs. Values support {{variable}} placeholders for dynamic composition.',
      ]},
      { type: 'header', text: 'Outputs (Where Results Go)' },
      { type: 'list', items: [
        '🗄️ SQL Writer — Insert or update rows in a SQL database table.',
        '🍃 NoSQL Database Writer — Write documents to MongoDB/DocumentDB.',
        '📄 Export PDF — Render results as a styled PDF document.',
        '📊 Export Excel — Generate an XLSX spreadsheet from your data.',
        '📋 Export CSV — Generate a CSV file from your data.',
        '✉️ Email Sender — Send an email with the results. Use record fields for dynamic recipients and content.',
        '☁️ S3 Writer — Write files to an AWS S3 bucket.',
        '� SFTP / FTP Writer — Upload files (JSON, CSV, Excel, text) to a remote SFTP, FTP, or FTPS server. Automatically creates directories.',
        '�🔗 Webhook Output — POST results to an external URL.',
      ]},
    ]
  },
  {
    id: 'agent-builder-dataflow',
    title: 'How Data Flows',
    icon: 'arrow-forward-outline',
    color: '#3b82f6',
    content: [
      { type: 'header', text: 'The Data Pipeline' },
      { type: 'text', text: 'Every node receives input data, processes it, and produces output data. The output of one node automatically becomes the input of the next node connected by an edge.' },
      { type: 'quote', text: 'Node A returns { "records": [...] } → Node B receives that exact object as its input data.' },
      { type: 'header', text: 'Input Rules' },
      { type: 'list', items: [
        'Root nodes (no incoming edges) receive the trigger data — what you pass when starting the workflow.',
        'A node with one parent receives that parent\'s output directly.',
        'A node with multiple parents receives an array of all parent outputs: [parent1_output, parent2_output, ...].',
      ]},
      { type: 'header', text: 'Using Data in Nodes' },
      { type: 'text', text: 'Different node types access input data in different ways:' },
      { type: 'list', items: [
        'LLM Processor — Use {{data}} in your prompt. It gets replaced with the JSON input.',
        'Python Code — The variable "data" contains your input. Write code like: result = [r for r in data["records"] if r["score"] > 80]',
        'Data Transform — Automatically operates on the "records" array in your input.',
        'Sources — Typically ignore input data and fetch fresh data from external systems.',
        'Outputs — Consume the input data for side effects (sending emails, writing to DB).',
      ]},
      { type: 'header', text: 'Variables & {{variable}} Substitution' },
      { type: 'text', text: 'Workflows have a "variables" dictionary that is shared across ALL nodes. Variables are initialized from the workflow definition, merged with trigger data, and updated by trigger schemas and Set Variable nodes.' },
      { type: 'list', items: [
        'Triggers — Define input parameters with the schema builder. Values entered at run time (or sent via webhook) become variables automatically.',
        '{{variable}} placeholders — Use {{name}} syntax in SQL queries, API URLs, file paths, LLM prompts, and Set Variable values. They get replaced with the corresponding variable value at execution time.',
        'Set Variable node — Dynamically create or update variables mid-flow. For example, compose a file path like /data/{{applicant_id}}/report.pdf.',
        'SQL queries use safe parameterized binding — {{variable}} becomes a :bind parameter, protecting against SQL injection.',
      ]},
      { type: 'tip', text: 'In Python Code nodes, access variables via the "variables" dictionary. In LLM prompts and node config fields, use {{variable_name}} for substitution.' },
      { type: 'header', text: 'Conditional Branching' },
      { type: 'text', text: 'Condition (If/Else) nodes evaluate an expression and route data down the TRUE or FALSE path. Only the matching downstream path executes — the other is skipped.' },
      { type: 'text', text: 'Switch Router nodes work similarly but support multiple output routes (out-0, out-1, out-2, etc.).' },
      { type: 'warning', text: 'When a node fails, the entire workflow stops at that point. Upstream node outputs are preserved. Enable retries in a node\'s config to automatically retry on failure.' },
    ]
  },
  {
    id: 'agent-builder-connections',
    title: 'Connections & Secrets',
    icon: 'key-outline',
    color: '#06b6d4',
    content: [
      { type: 'header', text: 'Connection Profiles' },
      { type: 'text', text: 'Connection profiles store database URLs, API keys, and credentials securely. You create them once in the Connections manager and reuse them across any number of workflows.' },
      { type: 'list', items: [
        'SQL connections — Store connection strings for PostgreSQL, MySQL, SQL Server.',
        'NoSQL connections — Store MongoDB/DocumentDB/CosmosDB connection strings.',
        'API connections — Store base URLs, auth headers, and API keys.',
        'SFTP / FTP connections — Store host, port, username, password, and optional SSH private key for file server access.',
      ]},
      { type: 'header', text: 'Test vs Production' },
      { type: 'text', text: 'Each connection profile has separate credentials for Test and Production environments. When you run a workflow in "Test" mode, it uses test credentials. When deployed and triggered live, it uses production credentials.' },
      { type: 'tip', text: 'Always test your workflow with test credentials first. Then deploy to production when you are confident it works correctly.' },
      { type: 'header', text: 'Using Connections in Nodes' },
      { type: 'text', text: 'Source and output nodes that need database or API access will show a "Connection" dropdown in their configuration panel. Select a saved connection profile — no need to paste credentials into every node.' },
      { type: 'warning', text: 'Connection secrets are encrypted at rest and masked in the UI. You will never see the full connection string after saving.' },
    ]
  },
  {
    id: 'agent-builder-examples',
    title: 'Workflow Examples',
    icon: 'bulb-outline',
    color: '#f59e0b',
    content: [
      { type: 'header', text: 'Example 1: Daily Sales Report Email' },
      { type: 'text', text: 'Automatically generate a sales summary every morning and email it to your team.' },
      { type: 'list', items: [
        '⏰ Scheduled Trigger — Cron: every day at 9:00 AM.',
        '🗄️ SQL Source — SELECT product, SUM(amount) as total FROM sales WHERE date = CURRENT_DATE GROUP BY product.',
        '🤖 LLM Processor — Prompt: "Write a concise sales summary for the following data: {{data}}". Output: text.',
        '✉️ Email Sender — To: team@company.com, Subject: "Daily Sales Report", Body: the LLM summary.',
      ]},
      { type: 'header', text: 'Example 2: Customer Feedback Classifier' },
      { type: 'text', text: 'Classify incoming customer feedback and route positive vs negative to different systems.' },
      { type: 'list', items: [
        '🔗 Webhook Trigger — Receives POST requests from your feedback form.',
        '🏷️ Classifier — Categories: ["positive", "negative", "neutral"]. Input: the feedback text.',
        '🔀 Condition — If classification == "negative" → TRUE path, else → FALSE path.',
        'TRUE → 🔗 Webhook Output — POST to your support ticket system.',
        'FALSE → 🍃 NoSQL Writer — Archive the positive/neutral feedback to MongoDB.',
      ]},
      { type: 'header', text: 'Example 3: Scheduled Data Sync' },
      { type: 'text', text: 'Sync data from a SQL database to a NoSQL database every hour with custom transformation.' },
      { type: 'list', items: [
        '⏰ Scheduled Trigger — Cron: every hour.',
        '🗄️ SQL Source — Fetch new/updated records since last sync.',
        '🐍 Python Code — Transform and clean the data: result = [{"_id": r["id"], "name": r["full_name"].title(), "updated": True} for r in data["records"]]',
        '🍃 NoSQL Writer — Upsert the transformed documents into MongoDB.',
      ]},
      { type: 'header', text: 'Example 4: AI Research Pipeline' },
      { type: 'text', text: 'Use an AI agent to research a topic and produce a polished PDF report.' },
      { type: 'list', items: [
        '🚀 Start Node — Input schema: topic (text), depth (select: "brief" / "detailed").',
        '🤖 AI Agent — Goal: "Research {{topic}} at {{depth}} level. Find key facts, statistics, and recent developments." Tools: web search, code execution.',
        '🤖 LLM Processor — Prompt: "Format the following research into a professional report with sections and citations: {{data}}".',
        '📄 Export PDF — Render the formatted report as a downloadable PDF.',
      ]},
      { type: 'tip', text: 'Start with a template from the Templates gallery and modify it to fit your use case. It is much faster than building from scratch!' },
    ]
  }
];

const HowToUseModal = ({ visible, onClose, initialSection }) => {
  const { theme } = useTheme();
  const [selectedSection, setSelectedSection] = useState(
    WIKI_CONTENT.find(s => s.id === initialSection) || WIKI_CONTENT[0]
  );
  const [isSidebarOpen, setIsSidebarOpen] = useState(width > 768); // Auto-open on desktop

  // Handle responsive layout & initialSection changes
  useEffect(() => {
    if (visible) {
      if (width > 768) setIsSidebarOpen(true);
      if (initialSection) {
        const section = WIKI_CONTENT.find(s => s.id === initialSection);
        if (section) setSelectedSection(section);
      }
    }
  }, [visible, initialSection]);

  const RenderContent = ({ block }) => {
    switch (block.type) {
      case 'header':
        return <Text style={[styles.contentHeader, { color: theme.text }]}>{block.text}</Text>;
      case 'text':
        return <Text style={[styles.contentText, { color: theme.textSecondary }]}>{block.text}</Text>;
      case 'quote':
        return (
          <View style={[styles.contentQuote, { borderLeftColor: theme.primary, backgroundColor: theme.surface }]}>
            <Text style={[styles.contentQuoteText, { color: theme.text }]}>{block.text}</Text>
          </View>
        );
      case 'list':
        return (
          <View style={styles.listContainer}>
            {block.items.map((item, idx) => (
              <View key={idx} style={styles.listItem}>
                <Ionicons name="ellipse" size={6} color={theme.textSecondary} style={{ marginTop: 8, marginRight: 8 }} />
                <Text style={[styles.listItemText, { color: theme.textSecondary }]}>{item}</Text>
              </View>
            ))}
          </View>
        );
      case 'tip':
        return (
          <View style={[styles.tipContainer, { backgroundColor: '#ECFDF5', borderColor: '#10B981' }]}>
            <Ionicons name="bulb" size={20} color="#10B981" style={{ marginRight: 8 }} />
            <Text style={[styles.tipText, { color: '#065F46' }]}>{block.text}</Text>
          </View>
        );
      case 'warning':
        return (
          <View style={[styles.tipContainer, { backgroundColor: '#FFFBEB', borderColor: '#F59E0B' }]}>
            <Ionicons name="warning" size={20} color="#F59E0B" style={{ marginRight: 8 }} />
            <Text style={[styles.tipText, { color: '#92400E' }]}>{block.text}</Text>
          </View>
        );
      default:
        return null;
    }
  };

  return (
    <Modal
      visible={visible}
      animationType="slide"
      presentationStyle="fullScreen"
      onRequestClose={onClose}
    >
      <SafeAreaView style={[styles.container, { backgroundColor: theme.background }]}>

        {/* TOP BAR */}
        <View style={[styles.topBar, { borderBottomColor: theme.border }]}>
          <TouchableOpacity onPress={onClose} style={styles.closeButton}>
            <Ionicons name="close" size={24} color={theme.text} />
          </TouchableOpacity>
          <Text style={[styles.topBarTitle, { color: theme.text }]}>Citra AI Wiki</Text>
          <TouchableOpacity onPress={() => setIsSidebarOpen(!isSidebarOpen)} style={styles.menuButton}>
            <Ionicons name={isSidebarOpen ? "book" : "menu"} size={24} color={theme.primary} />
          </TouchableOpacity>
        </View>

        <View style={styles.mainContainer}>

          {/* SIDEBAR */}
          {isSidebarOpen && (
            <View style={[styles.sidebar, { backgroundColor: theme.surface, borderRightColor: theme.border }]}>
              <ScrollView showsVerticalScrollIndicator={false}>
                <Text style={[styles.sidebarHeader, { color: theme.textSecondary }]}>Documentation</Text>
                {WIKI_CONTENT.map((section) => (
                  <TouchableOpacity
                    key={section.id}
                    style={[
                      styles.sidebarItem,
                      selectedSection.id === section.id && { backgroundColor: `${theme.primary}15` }
                    ]}
                    onPress={() => {
                      setSelectedSection(section);
                      if (width < 768) setIsSidebarOpen(false); // Close on mobile after selection
                    }}
                  >
                    <Ionicons
                      name={section.icon}
                      size={20}
                      color={selectedSection.id === section.id ? theme.primary : theme.textSecondary}
                      style={{ marginRight: 10 }}
                    />
                    <Text style={[
                      styles.sidebarItemText,
                      { color: selectedSection.id === section.id ? theme.primary : theme.text }
                    ]}>
                      {section.title}
                    </Text>
                  </TouchableOpacity>
                ))}
              </ScrollView>
            </View>
          )}

          {/* CONTENT AREA */}
          <ScrollView style={styles.contentArea} showsVerticalScrollIndicator={false}>
            <View style={styles.contentWrapper}>

              <View style={styles.contentIconWrapper}>
                <View style={[styles.contentIconBubble, { backgroundColor: `${selectedSection.color}20` }]}>
                  <Ionicons name={selectedSection.icon} size={40} color={selectedSection.color} />
                </View>
                <Text style={[styles.contentTitle, { color: theme.text }]}>{selectedSection.title}</Text>
              </View>

              <View style={[styles.divider, { backgroundColor: theme.border }]} />

              {selectedSection.content.map((block, index) => (
                <RenderContent key={index} block={block} />
              ))}

              <View style={{ height: 100 }} />
            </View>
          </ScrollView>

        </View>
      </SafeAreaView>
    </Modal>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
  },
  topBar: {
    height: 60,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 20,
    borderBottomWidth: 1,
  },
  topBarTitle: {
    fontSize: 18,
    fontWeight: '700',
  },
  closeButton: {
    padding: 8,
  },
  menuButton: {
    padding: 8,
  },
  mainContainer: {
    flex: 1,
    flexDirection: 'row',
  },
  sidebar: {
    width: Platform.OS === 'web' && width > 768 ? 300 : '100%',
    position: Platform.OS === 'web' && width > 768 ? 'relative' : 'absolute',
    height: '100%',
    zIndex: 10,
    borderRightWidth: 1,
    padding: 20,
  },
  sidebarHeader: {
    fontSize: 12,
    fontWeight: '700',
    textTransform: 'uppercase',
    letterSpacing: 1,
    marginBottom: 16,
    marginLeft: 12,
  },
  sidebarItem: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 12,
    paddingHorizontal: 16,
    borderRadius: 8,
    marginBottom: 4,
  },
  sidebarItemText: {
    fontSize: 15,
    fontWeight: '500',
  },
  contentArea: {
    flex: 1,
  },
  contentWrapper: {
    padding: 30,
    maxWidth: 800,
    alignSelf: 'center',
    width: '100%',
  },
  contentIconWrapper: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 24,
  },
  contentIconBubble: {
    width: 64,
    height: 64,
    borderRadius: 16,
    alignItems: 'center',
    justifyContent: 'center',
    marginRight: 20,
  },
  contentTitle: {
    fontSize: 32,
    fontWeight: '800',
    flex: 1,
  },
  divider: {
    height: 1,
    width: '100%',
    marginBottom: 32,
  },
  contentHeader: {
    fontSize: 22,
    fontWeight: '700',
    marginTop: 24,
    marginBottom: 12,
  },
  contentText: {
    fontSize: 16,
    lineHeight: 26,
    marginBottom: 16,
  },
  contentQuote: {
    padding: 20,
    borderLeftWidth: 4,
    borderRadius: 4,
    marginVertical: 16,
  },
  contentQuoteText: {
    fontSize: 18,
    fontStyle: 'italic',
    lineHeight: 28,
  },
  listContainer: {
    marginBottom: 16,
    paddingLeft: 8,
  },
  listItem: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    marginBottom: 8,
  },
  listItemText: {
    fontSize: 16,
    lineHeight: 24,
  },
  tipContainer: {
    flexDirection: 'row',
    padding: 16,
    borderRadius: 12,
    borderWidth: 1,
    marginVertical: 16,
    alignItems: 'flex-start',
  },
  tipText: {
    flex: 1,
    fontSize: 15,
    lineHeight: 22,
    fontWeight: '500',
  },
});

export default HowToUseModal;
