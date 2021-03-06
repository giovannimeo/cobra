# Copyright 2015 Cisco Systems, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from builtins import str
from builtins import object

try:
    from OpenSSL.crypto import FILETYPE_PEM, load_privatekey, sign
    inlineSignature = True
except ImportError:
    inlineSignature = False

# Always import these just for tests
import os
import tempfile
import subprocess
# This is used for inline signatures only
import base64
import time
import math

class AbstractSession(object):
    """Abstract session class
    
    Other sessions classes should derive from this class.
    
    Attributes:
      secure (bool): Only used for https. If True the remote server will be
        verified for authenticity.  If False the remote server will not be
        verified for authenticity - readonly

      timeout (int): Request timeout - readonly

      url (str): The APIC or fabric node URL - readonly

      formattype (str): The format type for the request - readonly

      formatStr (str): The format string for the request, either xml or json
        - readonly
    """
    XML_FORMAT, JSON_FORMAT = 0, 1

    def __init__(self, controllerUrl, secure, timeout, requestFormat):
        """Initialize an AbstractSession instance
        
        Args:
          controllerURL (str): The URL to reach the controller or fabric node
          secure (bool): Only used for https. If True the remote server will be
            verified for authenticity.  If False the remote server will not be
            verified for authenticity.
          timeout (int): Request timeout
          requestFormat (str): The format to send the request in.
            Valid values are xml or json.

        Raises:
          NotImplementedError: If the requestFormat is not valid
        """
        if requestFormat not in {'xml', 'json'}:
            raise NotImplementedError("requestFormat should be one of: %s" %
                                                             {'xml', 'json'})
        self.__secure = secure
        self.__timeout = timeout
        self.__controllerUrl = controllerUrl
        if requestFormat == 'xml':
            self.__format = AbstractSession.XML_FORMAT
        elif requestFormat == 'json':
            self.__format = AbstractSession.JSON_FORMAT

    @property
    def secure(self):
        return self.__secure

    @property
    def timeout(self):
        return self.__timeout

    @property
    def url(self):
        return self.__controllerUrl

    @property
    def formatType(self):
        return self.__format

    @property
    def formatStr(self):
        return 'xml' if self.__format == AbstractSession.XML_FORMAT else 'json'

    def login(self):
        """Login to the remote server.
        
        A generic login method that should be overridden by classes that derive
        from this class
        """
        pass

    def logout(self):
        """Logout from the remote server.
        
        A generic logout method that should be overridden by classes that
        derive from this class
        """
        pass

    def refresh(self):
        """Refresh the session to the remote server.
        
        A generic refresh method that should be overridden by classes that
        derive from this class
        """ 
        pass


class LoginError(Exception):
    """Represents exceptions that occur during logging in
    
    These exceptions usually involve a timeout or invalid authentication
    parameters
    """
    def __init__(self, errorCode, reasonStr):
        """Initialize a LoginError instance
        
        Args:
        errorCode (int): The error code for the exception
        reasonStr (str): A string indicating why the exception occurred
        """
        self.error = errorCode
        self.reason = reasonStr

    def __str__(self):
        return self.reason


class LoginSession(AbstractSession):
    """A login session with a username and password
    
    Note:
      The username and password are stored in memory.
      
    Attributes:
      user (str): The username to use for this session - readonly

      password (str): The password to use for this session - readonly

      cookie (str or None): The authentication cookie string for this session

      challenge (str or None): The authentication challenge string for this
        session

      version (str or None): The APIC software version returned once
        successfully logged in - readonly

      refreshTime (str or None): The relative login refresh time. The session
        must be refreshed by this time or it times out - readonly

      refreshTimeoutSeconds (str or None): The number of seconds for which this
        session is valid - readonly
        
      secure (bool): Only used for https. If True the remote server will be
        verified for authenticity.  If False the remote server will not be
        verified for authenticity - readonly

      timeout (int): Request timeout - readonly

      url (str): The APIC or fabric node URL - readonly

      formattype (str): The format type for the request - readonly

      formatStr (str): The format string for the request, either xml or json
        - readonly
    """

    def __init__(self, controllerUrl, user, password, secure=False, timeout=90,
                 requestFormat='xml'):
        """Initialize a LoginSession instance
        
        Args:
          controllerURL (str): The URL to reach the controller or fabric node
          user (str): The username to use to authenticate
          password (str): The password to use to authenticate
          secure (bool): Only used for https. If True the remote server will be
            verified for authenticity.  If False the remote server will not be
            verified for authenticity.
          timeout (int): Request timeout
          requestFormat (str): The format to send the request in.
            Valid values are xml or json.
        """
        super(LoginSession, self).__init__(controllerUrl, secure, timeout,
                                           requestFormat)
        self._user = user
        self._password = password
        self._cookie = None
        self._challenge = None
        self._version = None
        self._refreshTime = None
        self._refreshTimeoutSeconds = None

    @property
    def user(self):
        return self._user

    @property
    def password(self):
        return self._password

    @property
    def cookie(self):
        return self._cookie

    @cookie.setter
    def cookie(self, cookie):
        self._cookie = cookie

    @property
    def challenge(self):
        return self._challenge

    @challenge.setter
    def challenge(self, challenge):
        self._challenge = challenge

    @property
    def version(self):
        return self._version

    @property
    def refreshTime(self):
        return self._refreshTime

    @property
    def refreshTimeoutSeconds(self):
        return self._refreshTimeoutSeconds

    def getHeaders(self, uriPathAndOptions, data):
        """Get the HTTP headers for a given URI path and options string
        
        Args:
          uriPathAndOptions (str): The full URI path including the
            options string
          data (str): The payload

        Returns:
          dict: The headers for this session class
        """
        headers = {'Cookie': 'APIC-cookie=%s' % self.cookie}
        if self._challenge:
            headers['APIC-challenge'] = self._challenge
        return headers

    def _parseResponse(self, rsp):
        rspDict = rsp.json()
        data = rspDict.get('imdata', None)
        if not data:
            raise LoginError(0, 'Bad Response: ' + str(rsp.text))

        firstRecord = data[0]
        if 'error' in firstRecord:
            errorDict = firstRecord['error']
            reasonStr = errorDict['attributes']['text']
            errorCode = errorDict['attributes']['code']
            raise LoginError(errorCode, reasonStr)
        elif 'aaaLogin' in firstRecord:
            cookie = firstRecord['aaaLogin']['attributes']['token']
            refreshTimeoutSeconds = firstRecord['aaaLogin']['attributes']['refreshTimeoutSeconds']
            version = firstRecord['aaaLogin']['attributes']['version']
            self._cookie = cookie
            self._version = version
            self._refreshTime = int(refreshTimeoutSeconds) + math.trunc(time.time())
            self._refreshTimeoutSeconds = int(refreshTimeoutSeconds)
        else:
            raise LoginError(0, 'Bad Response: ' + str(rsp.text))


class CertSession(AbstractSession):

    """A session using a certificate dn and private key to generate signatures
    
    Attributes:
      certificateDn (str): The distingushed name (Dn) for the users X.509
        certificate - readonly

      privateKey (str): The private key to use when calculating signatures.
        Must be paired with the private key in the X.509 certificate - readonly

      cookie (str or None): The authentication cookie string for this session

      challenge (str or None): The authentication challenge string for this
        session

      version (str or None): The APIC software version returned once
        successfully logged in - readonly

      refreshTime (str or None): The relative login refresh time. The session
        must be refreshed by this time or it times out - readonly

      refreshTimeoutSeconds (str or None): The number of seconds for which this
        session is valid - readonly

      secure (bool): Only used for https. If True the remote server will be
        verified for authenticity.  If False the remote server will not be
        verified for authenticity - readonly

      timeout (int): Request timeout - readonly

      url (str): The APIC or fabric node URL - readonly

      formattype (str): The format type for the request - readonly

      formatStr (str): The format string for the request, either xml or json
        - readonly
    """

    def __init__(self, controllerUrl, certificateDn, privateKey, secure=False,
                 timeout=90, requestFormat='xml'):
        """Initialize a CertSession instance
        
        Args:
          controllerURL (str): The URL to reach the controller or fabric node
          certificateDn (str): The distinguished name of the users certificate
          privateKey (str): The private key to be used to calculate a signature
          secure (bool): Only used for https. If True the remote server will be
            verified for authenticity.  If False the remote server will not be
            verified for authenticity.
          timeout (int): Request timeout
          requestFormat (str): The format to send the request in.
            Valid values are xml or json.
        """
        super(CertSession, self).__init__(controllerUrl, secure, timeout,
                                          requestFormat)
        self.__certificateDn = certificateDn
        self.__privateKey = privateKey

    @property
    def certificateDn(self):
        return self.__certificateDn

    @property
    def privateKey(self):
        return self.__privateKey

    def getHeaders(self, uriPathAndOptions, data):
        """Get the HTTP headers for a given URI path and options string
        
        Args:
          uriPathAndOptions (str): The full URI path including the
            options string
          data (str): The payload

        Returns:
          dict: The headers for this session class
        """
        cookie = self._generateSignature(uriPathAndOptions, data)
        return {'Cookie': cookie}

    @staticmethod
    def runCmd(cmd):
        """Convenience method to run a command using subprocess

        Args:
          cmd (str): The command to run

        Returns:
          str: The output from the command

        Raises:
          subprocess.CalledProcessError: If an non-zero return code is sent by
            the process

        """
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        out, error = proc.communicate()
        if proc.returncode != 0:
            raise subprocess.CalledProcessError(proc.returncode,
                                                " ".join(cmd),
                                                out)
        return out

    @staticmethod
    def writeFile(fileName=None, mode="w", fileData=None):
        """Convenience method to write data to a file

        Args:
          fileName (str): The file to write to, default = None
          mode (str): The write mode, default = "w"
          fileData (varies): The data to write to the file
        """
        if fileName is None:
            return
        if fileData is None:
            fileData = ""
        with open(fileName, mode) as aFile:
            aFile.write(fileData)

    @staticmethod
    def readFile(fileName=None, mode="r"):
        """Convenience method to read some data from a file

        Args:
          fileName (str): The file to read from, default = None
          mode (str): The read mode, default = "r", Windows may require "rb"

        Returns:
          str: The data read from the file
        """
        if fileName is None:
            return ""
        with open(fileName, mode) as aFile:
            fileData = aFile.read()
        return fileData

    def _generateSignature(self, uri, data, forceManual=False):
        # One global that is not changing in the rest of the file is ok
        global inlineSignature
        # Added for easier testing of each signature generation method
        if forceManual:
            inlineSignature = False

        privateKeyStr = str(self.privateKey)
        certDn = str(self.certificateDn)

        if uri.endswith('?'):
            uri = uri[:-1]
        uri = uri.replace('//', '/')

        if inlineSignature:
            if data is None:
                payLoad = 'GET' + uri
            else:
                payLoad = 'POST' + uri + data

            pkey = load_privatekey(FILETYPE_PEM, privateKeyStr)

            signedDigest = sign(pkey, payLoad.encode(), 'sha256')
            signature = base64.b64encode(signedDigest).decode()
        else:
            tmpFiles = []
            tempDir = tempfile.mkdtemp()
            payloadFile = os.path.join(tempDir, "payload")
            keyFile = os.path.join(tempDir, "pkey")
            sigBinFile = keyFile + "_sig.bin"
            sigBaseFile = keyFile + "_sig.base64"

            if data is None:
                self.writeFile(payloadFile, mode="wt", fileData='GET' + uri)
            else:
                self.writeFile(payloadFile, mode="wt", fileData='POST' + uri +
                               data)
            tmpFiles.append(payloadFile)

            self.writeFile(fileName=keyFile, mode="w", fileData=privateKeyStr)
            tmpFiles.append(keyFile)

            cmd = ["openssl", "dgst", "-sha256", "-sign", keyFile, payloadFile]
            cmd_out = self.runCmd(cmd)
            self.writeFile(fileName=sigBinFile, mode="wb", fileData=cmd_out)
            tmpFiles.append(sigBinFile)

            cmd = ["openssl", "base64", "-in", keyFile + "_sig.bin", "-e",
                   "-out", sigBaseFile]
            self.runCmd(cmd)
            tmpFiles.append(sigBaseFile)

            sigBase64 = self.readFile(fileName=sigBaseFile)
            signature = "".join(sigBase64.splitlines())

            for fileName in tmpFiles:
                try:
                    os.remove(fileName)
                except:
                    pass
                try:
                    os.rmdir(tempDir)
                except:
                    pass

        cookieFmt = ("  APIC-Request-Signature=%s;" +
                     " APIC-Certificate-Algorithm=v1.0;" +
                     " APIC-Certificate-Fingerprint=fingerprint;" +
                     " APIC-Certificate-DN=%s")
        return cookieFmt % (signature, certDn)
