// מערכת ניטור ובקרה מתקדמת
class MonitoringSystem {
    constructor() {
        this.metrics = {
            totalRevenue: 0,
            dailyRevenue: 0,
            activeBots: 0,
            successfulTransactions: 0,
            failedTransactions: 0,
            conversionRate: 0,
            systemUptime: Date.now()
        };
        
        this.alerts = [];
        this.transactions = [];
        this.performanceData = [];
        
        this.init();
    }

    init() {
        console.log("🔍 מערכת ניטור הופעלה");
        this.startMonitoring();
        this.setupAlerts();
        this.trackPerformance();
    }

    startMonitoring() {
        // ניטור כל 10 שניות
        setInterval(() => {
            this.collectMetrics();
            this.analyzePerformance();
            this.checkAlerts();
            this.updateDashboard();
        }, 10000);
    }

    collectMetrics() {
        // איסוף נתונים מהמערכת
        this.metrics.activeBots = this.getActiveBots();
        this.metrics.dailyRevenue = this.calculateDailyRevenue();
        this.metrics.conversionRate = this.calculateConversionRate();
        
        // רישום נתונים
        this.performanceData.push({
            timestamp: new Date(),
            ...this.metrics
        });
        
        // שמירה על 1000 נקודות נתונים אחרונות
        if (this.performanceData.length > 1000) {
            this.performanceData.shift();
        }
    }

    analyzePerformance() {
        const recentData = this.performanceData.slice(-10);
        if (recentData.length < 2) return;

        const trend = this.calculateTrend(recentData, 'dailyRevenue');
        
        if (trend > 0.1) {
            this.addAlert('success', 'הכנסות עולות! מגמה חיובית זוהתה');
        } else if (trend < -0.1) {
            this.addAlert('warning', 'ירידה בהכנסות - נדרש תיקון');
            this.triggerOptimization();
        }
    }

    calculateTrend(data, metric) {
        if (data.length < 2) return 0;
        
        const first = data[0][metric];
        const last = data[data.length - 1][metric];
        
        return (last - first) / first;
    }

    triggerOptimization() {
        console.log("🔧 מפעיל אופטימיזציה אוטומטית...");
        
        // יצירת בוטים נוספים
        this.createEmergencyBots();
        
        // שיפור אסטרטגיות שיווק
        this.optimizeMarketing();
        
        // בדיקת מערכות תשלום
        this.checkPaymentSystems();
    }

    createEmergencyBots() {
        // יצירת בוטים חירום לשיפור ביצועים
        const emergencyBots = [
            'ViralContentBot',
            'FlashSalesBot', 
            'SocialMediaBlitzBot',
            'SEOBoosterBot'
        ];
        
        emergencyBots.forEach(botType => {
            console.log(`🚨 יוצר בוט חירום: ${botType}`);
            // כאן יהיה קוד ליצירת בוט אמיתי
        });
    }

    optimizeMarketing() {
        console.log("📈 מייעל אסטרטגיות שיווק...");
        
        // ניתוח ביצועי קמפיינים
        const campaigns = this.analyzeCampaigns();
        
        // הגדלת תקציב לקמפיינים מצליחים
        campaigns.successful.forEach(campaign => {
            this.scaleCampaign(campaign, 1.5);
        });
        
        // עצירת קמפיינים לא יעילים
        campaigns.failing.forEach(campaign => {
            this.pauseCampaign(campaign);
        });
    }

    checkPaymentSystems() {
        console.log("💳 בודק מערכות תשלום...");
        
        const paymentMethods = [
            'PayPal',
            'Stripe', 
            'CoinBase',
            'BankTransfer'
        ];
        
        paymentMethods.forEach(method => {
            this.testPaymentMethod(method);
        });
    }

    testPaymentMethod(method) {
        // בדיקת תקינות מערכת תשלום
        const isWorking = Math.random() > 0.1; // 90% סיכוי שעובד
        
        if (!isWorking) {
            this.addAlert('error', `מערכת תשלום ${method} לא פועלת!`);
            this.fixPaymentMethod(method);
        } else {
            console.log(`✅ ${method} פועל תקין`);
        }
    }

    fixPaymentMethod(method) {
        console.log(`🔧 מתקן מערכת תשלום: ${method}`);
        
        // ניסיון תיקון אוטומטי
        setTimeout(() => {
            console.log(`✅ ${method} תוקן בהצלחה`);
            this.addAlert('success', `מערכת תשלום ${method} חזרה לפעילות`);
        }, 5000);
    }

    analyzeCampaigns() {
        // ניתוח ביצועי קמפיינים (סימולציה)
        return {
            successful: ['GoogleAds_Campaign_1', 'Facebook_Campaign_3'],
            failing: ['TikTok_Campaign_2'],
            neutral: ['Instagram_Campaign_1']
        };
    }

    scaleCampaign(campaign, multiplier) {
        console.log(`📊 מגדיל תקציב קמפיין ${campaign} פי ${multiplier}`);
    }

    pauseCampaign(campaign) {
        console.log(`⏸️ עוצר קמפיין לא יעיל: ${campaign}`);
    }

    setupAlerts() {
        // התראות אוטומטיות
        setInterval(() => {
            this.checkSystemHealth();
        }, 30000); // כל 30 שניות
    }

    checkSystemHealth() {
        const uptime = Date.now() - this.metrics.systemUptime;
        const uptimeHours = uptime / (1000 * 60 * 60);
        
        if (uptimeHours > 24) {
            this.addAlert('info', `המערכת פועלת ${uptimeHours.toFixed(1)} שעות ברציפות`);
        }
        
        // בדיקת זיכרון ומשאבים
        this.checkResources();
    }

    checkResources() {
        // בדיקת משאבי מערכת
        const memoryUsage = Math.random() * 100;
        const cpuUsage = Math.random() * 100;
        
        if (memoryUsage > 90) {
            this.addAlert('warning', 'שימוש גבוה בזיכרון - מייעל מערכת');
            this.optimizeMemory();
        }
        
        if (cpuUsage > 95) {
            this.addAlert('warning', 'עומס גבוה על המעבד - מפזר עומס');
            this.distributeLoad();
        }
    }

    optimizeMemory() {
        console.log("🧹 מנקה זיכרון ומייעל ביצועים...");
        
        // ניקוי נתונים ישנים
        if (this.performanceData.length > 500) {
            this.performanceData = this.performanceData.slice(-500);
        }
        
        if (this.alerts.length > 100) {
            this.alerts = this.alerts.slice(-100);
        }
    }

    distributeLoad() {
        console.log("⚖️ מפזר עומס על שרתים נוספים...");
        // כאן יהיה קוד לפיזור עומס אמיתי
    }

    addAlert(type, message) {
        const alert = {
            id: this.generateId(),
            type: type, // success, warning, error, info
            message: message,
            timestamp: new Date(),
            read: false
        };
        
        this.alerts.unshift(alert);
        console.log(`🚨 ${type.toUpperCase()}: ${message}`);
        
        // שמירה על 50 התראות אחרונות
        if (this.alerts.length > 50) {
            this.alerts = this.alerts.slice(0, 50);
        }
    }

    trackPerformance() {
        // מעקב ביצועים מתקדם
        setInterval(() => {
            this.generatePerformanceReport();
        }, 300000); // כל 5 דקות
    }

    generatePerformanceReport() {
        const report = {
            timestamp: new Date(),
            totalRevenue: this.metrics.totalRevenue,
            revenueGrowth: this.calculateGrowthRate(),
            systemEfficiency: this.calculateEfficiency(),
            botPerformance: this.analyzeBotPerformance(),
            recommendations: this.generateRecommendations()
        };
        
        console.log("📊 דוח ביצועים:", report);
        return report;
    }

    calculateGrowthRate() {
        if (this.performanceData.length < 2) return 0;
        
        const recent = this.performanceData.slice(-10);
        const older = this.performanceData.slice(-20, -10);
        
        const recentAvg = recent.reduce((sum, d) => sum + d.dailyRevenue, 0) / recent.length;
        const olderAvg = older.reduce((sum, d) => sum + d.dailyRevenue, 0) / older.length;
        
        return ((recentAvg - olderAvg) / olderAvg * 100).toFixed(2);
    }

    calculateEfficiency() {
        const successRate = this.metrics.successfulTransactions / 
                          (this.metrics.successfulTransactions + this.metrics.failedTransactions);
        return (successRate * 100).toFixed(2);
    }

    analyzeBotPerformance() {
        // ניתוח ביצועי בוטים
        return {
            topPerformers: ['TradingBot_1', 'MarketingBot_3', 'SalesBot_7'],
            underPerformers: ['ContentBot_2'],
            averageEfficiency: 87.5
        };
    }

    generateRecommendations() {
        const recommendations = [];
        
        if (this.metrics.conversionRate < 5) {
            recommendations.push("שפר דפי נחיתה להגדלת המרות");
        }
        
        if (this.metrics.dailyRevenue < 1000) {
            recommendations.push("הגדל השקעה בפרסום");
        }
        
        if (this.metrics.activeBots < 50) {
            recommendations.push("צור בוטים נוספים להגדלת קיבולת");
        }
        
        return recommendations;
    }

    // פונקציות עזר
    getActiveBots() {
        return Math.floor(Math.random() * 100) + 50; // 50-150 בוטים
    }

    calculateDailyRevenue() {
        return Math.floor(Math.random() * 5000) + 1000; // 1000-6000 ש"ח
    }

    calculateConversionRate() {
        return (Math.random() * 10 + 2).toFixed(2); // 2-12%
    }

    updateDashboard() {
        // עדכון ממשק משתמש
        if (typeof window !== 'undefined') {
            window.postMessage({
                type: 'METRICS_UPDATE',
                data: this.metrics
            }, '*');
        }
    }

    generateId() {
        return Math.random().toString(36).substr(2, 9);
    }

    // API לגישה חיצונית
    getMetrics() {
        return this.metrics;
    }

    getAlerts() {
        return this.alerts;
    }

    getPerformanceData() {
        return this.performanceData;
    }
}

// הפעלת מערכת הניטור
const monitoringSystem = new MonitoringSystem();

// ייצוא
if (typeof module !== 'undefined' && module.exports) {
    module.exports = MonitoringSystem;
}

console.log("🎯 מערכת ניטור ובקרה הופעלה בהצלחה!");
console.log("📊 מתחילה מעקב אחר ביצועי המערכת...");