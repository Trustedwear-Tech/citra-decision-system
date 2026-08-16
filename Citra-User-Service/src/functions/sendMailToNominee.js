// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: BUSL-1.1
//
// Licensed under the Business Source License 1.1. Non-production use is granted;
// production use requires a commercial licence until the Change Date, after
// which this file converts to Apache-2.0. See LICENSE at the repository root.

const sendEmail = require('../shared/sendEmail');

const handler = async (request) => {
        try {
            // Parse the request body with timeout
            const requestData = await Promise.race([
                request.json(),
                new Promise((_, reject) => 
                    setTimeout(() => reject(new Error('Request parsing timeout')), 5000)
                )
            ]);
            
            const { deviceId, key } = requestData;
            
            // Validate deviceId
            if (!deviceId || typeof deviceId !== 'string') {
                return {
                    status: 400,
                    body: JSON.stringify({ 
                        success: false, 
                        error: 'Invalid or missing device ID' 
                    }),
                    headers: { 'Content-Type': 'application/json' }
                };
            }
            
            // Validate encryption key
            if (!key || typeof key !== 'string') {
                return {
                    status: 400,
                    body: JSON.stringify({ 
                        success: false, 
                        error: 'Invalid or missing encryption key' 
                    }),
                    headers: { 'Content-Type': 'application/json' }
                };
            }
            
            // Fetch nominees and sender details for the device
            const nominees = await getNomineeDetails(deviceId);
            const sender = await getSenderDetails(deviceId);
            
            if (!nominees || nominees.length === 0) {
                return {
                    status: 404,
                    body: JSON.stringify({ 
                        success: false, 
                        error: 'No nominees found for this device' 
                    }),
                    headers: { 'Content-Type': 'application/json' }
                };
            }
            
            // Log the attempt with details (without logging the actual key for security)
            console.log(`Attempting to send encryption key to ${nominees.length} nominees for device ${deviceId}`);
            
            // Send email to each nominee
            const emailResults = await sendEncryptionKeyEmails(nominees, deviceId, key, sender);
            
            return {
                status: 200,
                body: JSON.stringify({ 
                    success: true, 
                    message: emailResults.message,
                    failedEmails: emailResults.failedEmails || []
                }),
                headers: { 'Content-Type': 'application/json' }
            };
            
        } catch (error) {
            // Log detailed error information
            console.error('Error sending encryption key email:', error);
            
            // Provide more detailed error response
            let errorMessage = 'Error sending encryption key email';
            let status = 500;
            
            // Check for specific error types
            if (error.message && error.message.includes('timeout')) {
                errorMessage = 'Operation timed out. Please try again.';
                status = 503;
            } else if (error.message && error.message.includes('network')) {
                errorMessage = 'Network error, please check your connection';
                status = 503;
            } else if (error.message && error.message.includes('sender details')) {
                errorMessage = 'Could not find device owner details';
                status = 404;
            }
            
            return {
                status: status,
                body: JSON.stringify({ 
                    success: false, 
                    error: errorMessage,
                    details: process.env.NODE_ENV === 'development' ? error.message : undefined
                }),
                headers: { 'Content-Type': 'application/json' }
            };
        }
    }


/**
 * Fetches the sender details from the CUSTOMER table.
 */
async function getSenderDetails(deviceId) {
    const query = `SELECT Customer_Name, Mobile_Number, Email_Id FROM CUSTOMER WHERE user_id = ?`;
    try {
        const result = await executeQuery( query, [deviceId]);

        if (result && result.length > 0) {
            const sender = result[0];
            console.log("Sender details found:", sender);
            return sender;
        } else {
            throw new Error('No sender details found');
        }
    } catch (error) {
        console.error('Error fetching sender details:', error.stack || error);
        throw new Error('Database query failed when fetching sender details');
    }
}

/**
 * Fetches nominee details from the NOMINEE table.
 */
async function getNomineeDetails(deviceId) {
    const query = `SELECT Nominee_Name, Mobile_Number, Email_Id FROM NOMINEE WHERE user_id = ?`;
    try {
        const result = await executeQuery( query, [deviceId]);
        const nominees = [];

        if (result && result.length > 0) {
            result.forEach(record => {
                nominees.push({
                    nomineeName: record.Nominee_Name,
                    mobileNumber: record.Mobile_Number,
                    email: record.Email_Id
                });
            });
            console.log("Nominee details found:", nominees);
            return nominees;
        } else {
            return [];
        }
    } catch (error) {
        console.error('Error fetching nominee details:', error.stack || error);
        throw new Error('Database query failed when fetching nominees');
    }
}

/**
 * Sends encryption key emails to all nominees.
 */
async function sendEncryptionKeyEmails(nominees, deviceId, key, sender) {
    const failedEmails = [];
    console.log(`Starting to send encryption key emails to ${nominees.length} nominees`);
    
    const emailPromises = nominees.map(nominee => {
        // Create email content
        const plainTextContent = `
Hello ${nominee.nomineeName},

IMPORTANT: TRUSTED WEAR ENCRYPTION KEY

You have been nominated as a guardian by ${sender.Customer_Name}. In case of an emergency, their wearable device will send an SOS signal that will trigger a secure notification to you. This notification includes a link where you can access their current location, as well as real-time audio and video data.

Also, in a situation of emergency, if they cannot trigger SOS from their watch, you can visit https://citra-ai.com/ and log in using your registered email address.

ENCRYPTION KEY: ${key}

This is the AES encryption key used to protect audio and video data from the Trusted Wear device. You will need this key to access any encrypted emergency data.

PLEASE STORE THIS KEY SECURELY:
- Save it in a password manager or secure note
- Do not share it with unauthorized individuals
- This key is required to decrypt emergency audio/video data

If you have any questions or need further assistance, feel free to reach out to them:

Email: ${sender.Email_Id}
Mobile: ${sender.Mobile_Number}

Thank you for your support and for being there during critical times.

Best regards,
${sender.Customer_Name}

This email was sent by Trusted Wear Tech.
Visit our website: https://citra-ai.com
        `;
        
        const htmlContent = `
<div style="background-color: #f4f4f4; padding: 20px; font-family: Arial, sans-serif;">
    <div style="max-width: 600px; margin: auto; background-color: #fff; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
        <h2 style="color: #333; text-align: center;">Important Guardian Information</h2>
        <p style="font-size: 16px; color: #333;">Hello <strong>${nominee.nomineeName}</strong>,</p>
        <p style="font-size: 16px; color: #333;">
            You have been nominated as a guardian by <strong>${sender.Customer_Name}</strong>. In case of an emergency, my wearable device will send an SOS signal that will trigger a secure notification to you. This notification includes a link where you can access my current location, as well as real-time audio and video data.
        </p>
        <p style="font-size: 16px; color: #333;">
              Also, in a situation of my emergency, if I cannot trigger SOS from my watch, you can visit 
              <a href="https://citra-ai.com/" style="color: #007BFF; text-decoration: none;">Trusted Wear Tech</a>
              and log in using your registered email address.
        </p>
        
        <h3 style="color: #e74c3c; text-align: center;">ENCRYPTION KEY</h3>
        <div style="background-color: #f8f9fa; padding: 15px; border-radius: 5px; margin: 20px 0; border: 2px dashed #e74c3c;">
            <p style="font-family: monospace; background-color: #eee; padding: 10px; overflow-wrap: break-word; margin-bottom: 0; text-align: center;">${key}</p>
        </div>
        
        <p style="font-size: 16px; color: #333;">
            This is the AES encryption key used to protect audio and video data from the Trusted Wear device. You will need this key to access any encrypted emergency data.
        </p>
        
        <h3 style="color: #2c3e50;">PLEASE STORE THIS KEY SECURELY:</h3>
        <ul style="font-size: 16px; color: #333;">
            <li>Save it in a password manager or secure note</li>
            <li>Do not share it with unauthorized individuals</li>
            <li>This key is required to decrypt emergency audio/video data</li>
        </ul>
        
        <p style="font-size: 16px; color: #333;">
            If you have any questions or need further assistance, feel free to reach out to me:
        </p>
        <div style="background-color: #f8f9fa; padding: 15px; border-radius: 5px; margin: 20px 0; border-left: 4px solid #2c3e50;">
            <p style="margin: 5px 0;"><strong>Email:</strong> ${sender.Email_Id}</p>
            <p style="margin: 5px 0;"><strong>Mobile:</strong> ${sender.Mobile_Number}</p>
        </div>
        
        <p style="font-size: 16px; color: #333;">Thank you for your support and for being there during critical times.</p>
        <p style="font-size: 16px; color: #333;">Best regards,<br>${sender.Customer_Name}</p>
        
        <hr style="border: none; border-top: 1px solid #eee;">
        <p style="text-align: center; font-size: 12px; color: #aaa;">
            This email was sent by Trusted Wear Tech. <br>
            <a href="https://citra-ai.com" style="color: #007BFF; text-decoration: none;">Visit our website</a>
        </p>
    </div>
</div>
        `;

        console.log(`Attempting to send encryption key email to: ${nominee.email}`);
        
        return sendEmail({
            to: nominee.email,
            from: process.env.EMAIL_DEFAULT_SENDER || 'info@citra-ai.com',
            subject: 'Important: Trusted Wear Guardian Information and Encryption Key',
            text: plainTextContent,
            html: htmlContent
        }, context)
        .catch(emailError => {
            console.error(`Failed to send encryption key email to ${nominee.email}:`, emailError);
            failedEmails.push(nominee.email);
        });
    });
    
    await Promise.allSettled(emailPromises);
    
    if (failedEmails.length > 0) {
        console.log('The following emails failed to receive encryption key:', failedEmails);
        return { 
            message: `Encryption key sent successfully to ${nominees.length - failedEmails.length} out of ${nominees.length} nominees`,
            failedEmails 
        };
    } else {
        console.log('All encryption key emails sent successfully');
        return { message: "Encryption key sent successfully to all nominees" };
    }
}


module.exports = handler;



