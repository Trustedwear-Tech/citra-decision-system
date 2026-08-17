// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

class SEOService {
  constructor() {
    this.baseUrl = 'https://citra-ai.com';
    this.staticSeoPath = '/static-seo';
  }

  /**
   * Inject dynamic SEO meta tags for specific routes
   */
  injectMetaTags(pageData) {
    const { title, description, keywords, ogImage, canonicalUrl } = pageData;

    // Update existing meta tags or create new ones
    this.updateMetaTag('title', title);
    this.updateMetaTag('meta[name="description"]', description, 'content');
    this.updateMetaTag('meta[name="keywords"]', keywords, 'content');
    this.updateMetaTag('meta[property="og:title"]', title, 'content');
    this.updateMetaTag('meta[property="og:description"]', description, 'content');
    this.updateMetaTag('meta[property="og:image"]', ogImage || `${this.baseUrl}/images/og-default.png`, 'content');
    this.updateMetaTag('meta[property="og:url"]', canonicalUrl, 'content');
    this.updateMetaTag('meta[name="twitter:title"]', title, 'content');
    this.updateMetaTag('meta[name="twitter:description"]', description, 'content');
    this.updateMetaTag('link[rel="canonical"]', canonicalUrl, 'href');
  }

  updateMetaTag(selector, content, attribute = 'textContent') {
    let element = document.querySelector(selector);
    
    if (!element) {
      // Create new meta tag if it doesn't exist
      if (selector.includes('meta')) {
        element = document.createElement('meta');
        if (selector.includes('name=')) {
          const nameMatch = selector.match(/name="([^"]+)"/);
          if (nameMatch) element.setAttribute('name', nameMatch[1]);
        }
        if (selector.includes('property=')) {
          const propMatch = selector.match(/property="([^"]+)"/);
          if (propMatch) element.setAttribute('property', propMatch[1]);
        }
        document.head.appendChild(element);
      } else if (selector.includes('link')) {
        element = document.createElement('link');
        if (selector.includes('rel=')) {
          const relMatch = selector.match(/rel="([^"]+)"/);
          if (relMatch) element.setAttribute('rel', relMatch[1]);
        }
        document.head.appendChild(element);
      } else if (selector === 'title') {
        element = document.createElement('title');
        document.head.appendChild(element);
      }
    }

    if (element) {
      if (attribute === 'textContent') {
        element.textContent = content;
      } else {
        element.setAttribute(attribute, content);
      }
    }
  }

  /**
   * Setup structured data for the current page
   */
  injectStructuredData(data) {
    // Remove existing structured data
    const existingScript = document.querySelector('script[type="application/ld+json"]');
    if (existingScript) {
      existingScript.remove();
    }

    // Add new structured data
    const script = document.createElement('script');
    script.type = 'application/ld+json';
    script.textContent = JSON.stringify(data);
    document.head.appendChild(script);
  }

  /**
   * Setup SEO for different app routes/screens
   */
  setupRouteSpecificSEO() {
    const currentPath = window.location.pathname;
    const hash = window.location.hash;

    // Determine the current screen/route
    let seoData = this.getDefaultSEOData();

    if (currentPath.includes('/app') || hash.includes('chat')) {
      seoData = this.getChatSEOData();
    } else if (currentPath.includes('/blog')) {
      seoData = this.getBlogSEOData();
    } else if (hash.includes('legal') || currentPath.includes('legal')) {
      seoData = this.getLegalSEOData();
    } else if (hash.includes('healthcare') || currentPath.includes('healthcare')) {
      seoData = this.getHealthcareSEOData();
    } else if (hash.includes('professionals') || currentPath.includes('professionals')) {
      seoData = this.getProfessionalsSEOData();
    } else if (hash.includes('features') || currentPath.includes('features')) {
      seoData = this.getFeaturesSEOData();
    }

    this.injectMetaTags(seoData.meta);
    this.injectStructuredData(seoData.structuredData);
  }

  getDefaultSEOData() {
    return {
      meta: {
        title: "Citra AI — Privacy-First AI Platform | Open-Source Models | Private Cloud",
        description: "Citra AI is a privacy-first AI platform built on open-source models — available in your private cloud. Deploy workflow agents and decision apps that reason over your data with complete data sovereignty.",
        keywords: "privacy-first AI platform, open-source AI models, private cloud AI, data sovereignty, enterprise AI, Citra Enterprise, Citra Consulting, workflow automation AI, AI agents, AI decision apps, Citra Vault",
        canonicalUrl: `${this.baseUrl}/`
      },
      structuredData: {
        "@context": "https://schema.org",
        "@type": "WebApplication",
        "name": "Citra AI",
        "url": this.baseUrl,
        "description": "Privacy-first AI platform built on open-source models. Deploy in your private cloud with complete data sovereignty. Workflow agents and decision apps — all grounded on your data.",
        "applicationCategory": "ProductivityApplication",
        "operatingSystem": ["Web", "iOS", "Android"]
      }
    };
  }

  getChatSEOData() {
    return {
      meta: {
        title: "AI Chat — Privacy-First AI Assistant | Citra AI",
        description: "Chat with a privacy-first AI assistant built on open-source models. Search your secure Citra Vault, get instant answers with citations, and keep full control of your data.",
        keywords: "privacy-first AI chat, open-source AI assistant, private document search, Citra Vault, AI knowledge base, secure AI chat",
        canonicalUrl: `${this.baseUrl}/app`
      },
      structuredData: {
        "@context": "https://schema.org",
        "@type": "ChatBot",
        "name": "Citra AI Assistant",
        "description": "Privacy-first AI assistant built on open-source models with secure document knowledge retrieval from your Citra Vault"
      }
    };
  }

  getBlogSEOData() {
    return {
      meta: {
        title: "Blog — Privacy-First AI, Workflow Agents & Enterprise AI Insights | Citra AI",
        description: "Insights on privacy-first AI, open-source models, workflow automation, private-cloud deployment, and enterprise AI strategy from the Citra AI team.",
        keywords: "Citra AI blog, privacy-first AI, open-source AI models, workflow agents, private cloud AI, enterprise AI, AI automation, Citra Consulting",
        canonicalUrl: `${this.baseUrl}/blog/`
      },
      structuredData: {
        "@context": "https://schema.org",
        "@type": "Blog",
        "name": "Citra AI Blog",
        "url": `${this.baseUrl}/blog/`,
        "description": "Insights on privacy-first AI, open-source models, workflow automation, and enterprise AI deployment",
        "publisher": {
          "@type": "Organization",
          "name": "Trustedwear Tech Private Limited"
        }
      }
    };
  }

  getHealthcareSEOData() {
    return {
      meta: {
        title: "AI Clinical Assistant for Healthcare Providers - Citra AI",
        description: "Enhance patient care with AI-powered medical knowledge management and clinical decision support. HIPAA-compliant with secure medical document analysis.",
        keywords: "healthcare ai, clinical assistant, medical ai, hipaa compliant, clinical decision support, medical document ai, patient care ai",
        canonicalUrl: `${this.baseUrl}/healthcare`
      },
      structuredData: {
        "@context": "https://schema.org",
        "@type": "MedicalWebPage",
        "name": "Healthcare AI Clinical Assistant",
        "description": "AI-powered clinical assistant for healthcare providers with HIPAA-compliant medical document analysis",
        "medicalAudience": {
          "@type": "MedicalAudience",
          "audienceType": "Healthcare Professional"
        }
      }
    };
  }

  getProfessionalsSEOData() {
    return {
      meta: {
        title: "AI Legal Assistant & Professional Document AI - Citra AI",
        description: "AI-powered document management for legal professionals, teachers, accountants, engineers. Secure professional knowledge base with instant document search.",
        keywords: "legal ai assistant, professional document ai, legal research ai, professional productivity, document management ai, case management ai",
        canonicalUrl: `${this.baseUrl}/professionals`
      },
      structuredData: {
        "@context": "https://schema.org",
        "@type": "ProfessionalService",
        "name": "Professional AI Document Assistant",
        "description": "AI assistant for professional document management and knowledge retrieval across legal, education, engineering, and business sectors"
      }
    };
  }

  getFeaturesSEOData() {
    return {
      meta: {
        title: "Features — Privacy-First AI Platform | Workflow Agents, Vault, Decision Apps | Citra AI",
        description: "Explore Citra AI features: privacy-first Citra Vault, workflow agents, AI decision apps, open-source models, private-cloud deployment, and enterprise-grade security.",
        keywords: "privacy-first AI features, Citra Vault, workflow agents, AI decision apps, open-source AI, private cloud AI, data sovereignty, enterprise AI security",
        canonicalUrl: `${this.baseUrl}/features`
      },
      structuredData: {
        "@context": "https://schema.org",
        "@type": "SoftwareApplication",
        "name": "Citra AI Features",
        "featureList": [
          "Privacy-First Citra Vault",
          "Workflow Agents & Automation",
          "Open-Source Model Support",
          "Private Cloud Deployment",
          "Deep Research & Internet Search",
          "Meeting & Audio Recording",
          "Enterprise-Grade Security & Data Sovereignty"
        ]
      }
    };
  }

  getLegalSEOData() {
    return {
      meta: {
        title: "AI Legal Assistant for Modern Law Practice - Citra AI",
        description: "Transform legal research and case management with AI-powered document analysis. Attorney-client privilege protection with enterprise-grade security for law firms.",
        keywords: "legal ai assistant, ai legal research, case management ai, legal document analysis, attorney ai, law firm ai, legal precedent search",
        canonicalUrl: `${this.baseUrl}/legal`
      },
      structuredData: {
        "@context": "https://schema.org",
        "@type": "LegalService",
        "name": "AI Legal Assistant",
        "description": "AI-powered legal research and case management with attorney-client privilege protection",
        "serviceType": "Legal Technology"
      }
    };
  }

  /**
   * Handle bot detection and redirect logic
   */
  handleBotDetection() {
    const userAgent = navigator.userAgent.toLowerCase();
    const isBot = this.isCrawlerBot(userAgent);
    
    if (isBot) {
      // Don't redirect bots, let them see the current content
      console.log('🤖 Search engine bot detected, serving current content');
      return false;
    }

    // Check if this is a redirect from static SEO page
    const fromSEORedirect = sessionStorage.getItem('seo-redirect');
    if (fromSEORedirect) {
      sessionStorage.removeItem('seo-redirect');
      console.log('🔄 Returned from SEO page, setting up dynamic SEO');
    }

    return !isBot;
  }

  isCrawlerBot(userAgent) {
    const botPatterns = [
      'googlebot', 'bingbot', 'slurp', 'duckduckbot', 'baiduspider',
      'yandexbot', 'facebookexternalhit', 'twitterbot', 'linkedinbot',
      'whatsapp', 'telegram', 'skype', 'crawler', 'spider', 'bot'
    ];
    
    return botPatterns.some(pattern => userAgent.includes(pattern));
  }

  /**
   * Generate dynamic sitemap based on app routes
   */
  generateDynamicSitemap() {
    const routes = [
      { path: '/', priority: 1.0, changefreq: 'weekly' },
      { path: '/app', priority: 0.9, changefreq: 'daily' },
      { path: '/features', priority: 0.8, changefreq: 'monthly' },
      { path: '/healthcare', priority: 0.8, changefreq: 'monthly' },
      { path: '/professionals', priority: 0.8, changefreq: 'monthly' },
      { path: '/faq', priority: 0.6, changefreq: 'monthly' }
    ];

    return routes.map(route => ({
      url: `${this.baseUrl}${route.path}`,
      lastmod: new Date().toISOString().split('T')[0],
      changefreq: route.changefreq,
      priority: route.priority
    }));
  }

  /**
   * Monitor page performance for SEO
   */
  monitorPagePerformance() {
    if ('performance' in window) {
      window.addEventListener('load', () => {
        setTimeout(() => {
          const perfData = performance.getEntriesByType('navigation')[0];
          const metrics = {
            loadTime: perfData.loadEventEnd - perfData.loadEventStart,
            domContentLoaded: perfData.domContentLoadedEventEnd - perfData.domContentLoadedEventStart,
            firstPaint: this.getFirstPaint(),
            pageSize: this.getPageSize()
          };

          // Log performance metrics for SEO optimization
          console.log('📊 SEO Performance Metrics:', metrics);
          
          // Report slow loading if needed
          if (metrics.loadTime > 3000) {
            console.warn('⚠️ Page load time exceeds 3 seconds, may impact SEO');
          }
        }, 100);
      });
    }
  }

  getFirstPaint() {
    const paintEntries = performance.getEntriesByType('paint');
    const firstPaint = paintEntries.find(entry => entry.name === 'first-paint');
    return firstPaint ? firstPaint.startTime : null;
  }

  getPageSize() {
    const resources = performance.getEntriesByType('resource');
    return resources.reduce((total, resource) => total + (resource.transferSize || 0), 0);
  }

  /**
   * Setup Open Graph image generation for dynamic content
   */
  setupDynamicOGImages() {
    // For SPA, we can generate dynamic OG images based on content
    const canvas = document.createElement('canvas');
    canvas.width = 1200;
    canvas.height = 630;
    const ctx = canvas.getContext('2d');

    // Generate OG image with current page content
    ctx.fillStyle = '#6366f1';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    
    ctx.fillStyle = 'white';
    ctx.font = 'bold 60px Arial';
    ctx.textAlign = 'center';
    ctx.fillText('Citra AI', canvas.width / 2, canvas.height / 2);

    // Convert to data URL and update OG image
    const dataUrl = canvas.toDataURL('image/png');
    this.updateMetaTag('meta[property="og:image"]', dataUrl, 'content');
  }

  /**
   * Initialize SEO service
   */
  init() {
    console.log('🔍 Initializing SEO Service...');
    
    // Setup route-specific SEO
    this.setupRouteSpecificSEO();
    
    // Handle bot detection
    const shouldRedirect = this.handleBotDetection();
    
    // Monitor performance
    this.monitorPagePerformance();
    
    // Listen for route changes in SPA
    this.listenForRouteChanges();
    
    console.log('✅ SEO Service initialized');
    return shouldRedirect;
  }

  listenForRouteChanges() {
    // Listen for hash changes (if using hash routing)
    window.addEventListener('hashchange', () => {
      this.setupRouteSpecificSEO();
    });

    // Listen for popstate events (if using history API)
    window.addEventListener('popstate', () => {
      this.setupRouteSpecificSEO();
    });
  }

  /**
   * Preload critical SEO resources
   */
  preloadSEOResources() {
    const criticalResources = [
      '/favicon.ico'
    ];

    criticalResources.forEach(resource => {
      const link = document.createElement('link');
      link.rel = 'preload';
      link.as = 'image';
      link.href = resource;
      
      // Add error handler to prevent unhandled promise rejections
      link.onerror = (err) => {
        console.warn(`Failed to preload SEO resource: ${resource}`, err);
      };
      
      document.head.appendChild(link);
    });
  }
}

// Export for use in other modules
if (typeof module !== 'undefined' && module.exports) {
  module.exports = SEOService;
} else if (typeof window !== 'undefined') {
  window.SEOService = SEOService;
}
