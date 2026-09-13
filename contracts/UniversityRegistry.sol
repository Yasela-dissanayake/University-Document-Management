// SPDX-License-Identifier: MIT
pragma solidity =0.8.28;

/**
 * @title UniversityRegistry
 * @notice University registry supporting:
 *         1. Student records
 *         2. Historical semester records
 *         3. Immutable document version management
 *         4. Document approval metadata
 *         5. Authorized backend write access
 *
 * @dev The existing student/semester API is preserved for backward
 *      compatibility. A separate document-version layer is added to
 *      support the updated blockchain-AI architecture.
 */
contract UniversityRegistry {

    // ============================================================
    // Access Control
    // ============================================================

    address public owner;

    /// @dev Addresses allowed to call write functions.
    mapping(address => bool) public allowedCallers;

    event CallerAllowed(address indexed caller, bool allowed);

    event OwnershipTransferred(
        address indexed previousOwner,
        address indexed newOwner
    );

    constructor() {
        owner = msg.sender;
        allowedCallers[msg.sender] = true;
    }

    modifier onlyOwner() {
        require(
            msg.sender == owner,
            "UniversityRegistry: caller is not owner"
        );
        _;
    }

    modifier onlyAuthorized() {
        require(
            msg.sender == owner || allowedCallers[msg.sender],
            "UniversityRegistry: caller is not authorized"
        );
        _;
    }

    /// @notice Grant or revoke write access for a caller address.
    function setCallerAllowed(
        address caller,
        bool allowed
    ) external onlyOwner {
        allowedCallers[caller] = allowed;
        emit CallerAllowed(caller, allowed);
    }

    /// @notice Transfer ownership to a new address.
    function transferOwnership(
        address newOwner
    ) external onlyOwner {
        require(
            newOwner != address(0),
            "UniversityRegistry: zero address"
        );

        emit OwnershipTransferred(owner, newOwner);

        owner = newOwner;
        allowedCallers[newOwner] = true;
    }


    // ============================================================
    // Existing Student Data Structures
    // ============================================================

    struct StudentRecord {
        string studentId;
        string name;
        string program;
        uint8 year;

        // Latest document reference
        string documentsIPFSHash;
        bytes32 contentHash;
        uint256 timestamp;

        bool isActive;
        address studentWallet;

        // Existing historical semester records
        string[] semesterHashes;
        bytes32[] contentHashes;
        uint256[] timestamps;
    }

    /**
     * @dev Existing linked-list structure used for student enumeration.
     *      This is NOT used for document versioning.
     */
    struct DocumentNode {
        string prev;
        string next;
        bool exists;
    }

    mapping(string => StudentRecord) public students;

    mapping(string => DocumentNode) private studentLinks;

    string[] public studentIds;

    string public head;
    string public tail;

    uint256 public studentCount;


    // ============================================================
    // New Document Version Data Structures
    // ============================================================

    /**
     * @notice Represents a logical document.
     *
     * Example:
     *     documentId   = "S19357-TRANSCRIPT"
     *     studentId    = "S19357"
     *     documentType = "Transcript"
     *     latestVersion = 3
     */
    struct DocumentRecord {
        string documentId;
        string studentId;
        string documentType;
        uint256 latestVersion;
        bool exists;
    }

    /**
     * @notice Represents one immutable version of a document.
     *
     * Version 1:
     *     previousVersion = 0
     *
     * Version 2:
     *     previousVersion = 1
     *
     * Version 3:
     *     previousVersion = 2
     */
    struct DocumentVersion {
        uint256 version;
        uint256 previousVersion;

        string ipfsCID;
        bytes32 contentHash;

        string approvalStatus;
        string approvedBy;

        uint256 timestamp;
    }

    /**
     * @dev Document-level records indexed by document ID.
     */
    mapping(string => DocumentRecord) public documents;

    /**
     * @dev Document versions indexed by:
     *
     *     documentId => version => DocumentVersion
     */
    mapping(string => mapping(uint256 => DocumentVersion))
        private documentVersions;


    // ============================================================
    // Existing Events
    // ============================================================

    event StudentRegistered(
        string indexed studentId,
        string name,
        string program,
        string documentsIPFSHash
    );

    event DocumentUpdated(
        string indexed studentId,
        string oldIPFSHash,
        string newIPFSHash,
        address updatedBy
    );

    event DocumentVerified(
        string indexed studentId,
        string ipfsHash,
        address verifiedBy,
        uint256 timestamp
    );


    // ============================================================
    // New Document Version Events
    // ============================================================

    /**
     * @notice Emitted whenever a new immutable document version
     *         is recorded.
     */
    event DocumentVersionCreated(
        string indexed documentId,
        string indexed studentId,
        uint256 version,
        uint256 previousVersion,
        string ipfsCID,
        bytes32 contentHash,
        string approvalStatus,
        string approvedBy,
        uint256 timestamp
    );


    // ============================================================
    // Existing Modifiers
    // ============================================================

    modifier studentExists(
        string memory _studentId
    ) {
        require(
            bytes(students[_studentId].studentId).length > 0,
            "UniversityRegistry: student not found"
        );
        _;
    }


    // ============================================================
    // Existing Student Functions
    // ============================================================

    /**
     * @notice Register a new student with their first semester record.
     * @param _year Academic year, must be between 1 and 10.
     */
    function registerStudent(
        string memory _studentId,
        string memory _name,
        string memory _program,
        uint8 _year,
        string memory _documentsIPFSHash,
        bytes32 _contentHash,
        address _studentWallet
    )
        public
        onlyAuthorized
    {
        require(
            bytes(students[_studentId].studentId).length == 0,
            "UniversityRegistry: student already registered"
        );

        require(
            _year >= 1 && _year <= 10,
            "UniversityRegistry: year must be 1-10"
        );

        require(
            bytes(_studentId).length > 0,
            "UniversityRegistry: empty student ID"
        );

        StudentRecord storage s = students[_studentId];

        s.studentId = _studentId;
        s.name = _name;
        s.program = _program;
        s.year = _year;

        s.documentsIPFSHash = _documentsIPFSHash;
        s.contentHash = _contentHash;
        s.timestamp = block.timestamp;

        s.isActive = true;
        s.studentWallet = _studentWallet;

        // Existing semester/versioning arrays
        s.semesterHashes.push(_documentsIPFSHash);
        s.contentHashes.push(_contentHash);
        s.timestamps.push(block.timestamp);

        studentIds.push(_studentId);

        // Maintain existing doubly-linked student list
        if (studentCount == 0) {
            head = _studentId;
            tail = _studentId;
        } else {
            studentLinks[tail].next = _studentId;
            studentLinks[_studentId].prev = tail;
            tail = _studentId;
        }

        studentLinks[_studentId].exists = true;

        studentCount++;

        emit StudentRegistered(
            _studentId,
            _name,
            _program,
            _documentsIPFSHash
        );
    }


    /**
     * @notice Append a new semester record for an existing student.
     *
     * @dev Existing functionality preserved for backward compatibility.
     *      The new document-version API should be used for explicit
     *      document version management.
     */
    function addSemesterRecord(
        string memory _studentId,
        string memory _ipfsHash,
        bytes32 _contentHash
    )
        public
        onlyAuthorized
        studentExists(_studentId)
    {
        require(
            bytes(_ipfsHash).length > 0,
            "UniversityRegistry: empty IPFS hash"
        );

        StudentRecord storage s = students[_studentId];

        string memory oldHash = s.documentsIPFSHash;

        s.semesterHashes.push(_ipfsHash);
        s.contentHashes.push(_contentHash);
        s.timestamps.push(block.timestamp);

        // Update latest reference
        s.documentsIPFSHash = _ipfsHash;
        s.contentHash = _contentHash;
        s.timestamp = block.timestamp;

        emit DocumentUpdated(
            _studentId,
            oldHash,
            _ipfsHash,
            msg.sender
        );
    }


    // ============================================================
    // New Document Version Functions
    // ============================================================

    /**
     * @notice Create a new immutable version of a document.
     *
     * @dev The contract determines the version number automatically.
     *
     *      First version:
     *          version = 1
     *          previousVersion = 0
     *
     *      Subsequent version:
     *          version = latestVersion + 1
     *          previousVersion = latestVersion
     *
     * @param _documentId Unique logical document identifier.
     * @param _studentId Student associated with the document.
     * @param _documentType Type of document, e.g. Transcript.
     * @param _ipfsCID IPFS CID containing the encrypted document.
     * @param _contentHash SHA-256/content hash represented as bytes32.
     * @param _approvalStatus Approval state of this version.
     * @param _approvedBy Entity/person responsible for approval.
     */
    function createDocumentVersion(
        string memory _documentId,
        string memory _studentId,
        string memory _documentType,
        string memory _ipfsCID,
        bytes32 _contentHash,
        string memory _approvalStatus,
        string memory _approvedBy
    )
        public
        onlyAuthorized
        studentExists(_studentId)
        returns (uint256 newVersion)
    {
        require(
            bytes(_documentId).length > 0,
            "UniversityRegistry: empty document ID"
        );

        require(
            bytes(_documentType).length > 0,
            "UniversityRegistry: empty document type"
        );

        require(
            bytes(_ipfsCID).length > 0,
            "UniversityRegistry: empty IPFS CID"
        );

        require(
            bytes(_approvalStatus).length > 0,
            "UniversityRegistry: empty approval status"
        );

        DocumentRecord storage document = documents[_documentId];

        // --------------------------------------------------------
        // First version
        // --------------------------------------------------------

        if (!document.exists) {

            document.documentId = _documentId;
            document.studentId = _studentId;
            document.documentType = _documentType;

            document.latestVersion = 1;
            document.exists = true;

            documentVersions[_documentId][1] = DocumentVersion({
                version: 1,
                previousVersion: 0,
                ipfsCID: _ipfsCID,
                contentHash: _contentHash,
                approvalStatus: _approvalStatus,
                approvedBy: _approvedBy,
                timestamp: block.timestamp
            });

            emit DocumentVersionCreated(
                _documentId,
                _studentId,
                1,
                0,
                _ipfsCID,
                _contentHash,
                _approvalStatus,
                _approvedBy,
                block.timestamp
            );

            return 1;
        }

        // --------------------------------------------------------
        // Existing document
        // --------------------------------------------------------

        require(
            keccak256(
                bytes(document.studentId)
            ) ==
            keccak256(
                bytes(_studentId)
            ),
            "UniversityRegistry: student mismatch"
        );

        require(
            keccak256(
                bytes(document.documentType)
            ) ==
            keccak256(
                bytes(_documentType)
            ),
            "UniversityRegistry: document type mismatch"
        );

        uint256 previousVersion = document.latestVersion;

        newVersion = previousVersion + 1;

        document.latestVersion = newVersion;

        documentVersions[_documentId][newVersion] = DocumentVersion({
            version: newVersion,
            previousVersion: previousVersion,
            ipfsCID: _ipfsCID,
            contentHash: _contentHash,
            approvalStatus: _approvalStatus,
            approvedBy: _approvedBy,
            timestamp: block.timestamp
        });

        emit DocumentVersionCreated(
            _documentId,
            _studentId,
            newVersion,
            previousVersion,
            _ipfsCID,
            _contentHash,
            _approvalStatus,
            _approvedBy,
            block.timestamp
        );

        return newVersion;
    }


    /**
     * @notice Check whether a document exists.
     */
    function documentExists(
        string memory _documentId
    )
        public
        view
        returns (bool)
    {
        return documents[_documentId].exists;
    }


    /**
     * @notice Get a specific document version.
     */
    function getDocumentVersion(
        string memory _documentId,
        uint256 _version
    )
        public
        view
        returns (
            uint256 version,
            uint256 previousVersion,
            string memory ipfsCID,
            bytes32 contentHash,
            string memory approvalStatus,
            string memory approvedBy,
            uint256 timestamp
        )
    {
        require(
            documents[_documentId].exists,
            "UniversityRegistry: document not found"
        );

        require(
            _version > 0 &&
            _version <= documents[_documentId].latestVersion,
            "UniversityRegistry: invalid document version"
        );

        DocumentVersion memory d =
            documentVersions[_documentId][_version];

        return (
            d.version,
            d.previousVersion,
            d.ipfsCID,
            d.contentHash,
            d.approvalStatus,
            d.approvedBy,
            d.timestamp
        );
    }


    /**
     * @notice Get the latest version of a document.
     *
     * @dev This returns the latest recorded version, regardless of
     *      approval status.
     *
     *      Use getLatestApprovedDocumentVersion() when retrieval
     *      must be restricted to an approved version.
     */
    function getLatestDocumentVersion(
        string memory _documentId
    )
        public
        view
        returns (
            uint256 version,
            uint256 previousVersion,
            string memory ipfsCID,
            bytes32 contentHash,
            string memory approvalStatus,
            string memory approvedBy,
            uint256 timestamp
        )
    {
        require(
            documents[_documentId].exists,
            "UniversityRegistry: document not found"
        );

        uint256 latest = documents[_documentId].latestVersion;

        DocumentVersion memory d =
            documentVersions[_documentId][latest];

        return (
            d.version,
            d.previousVersion,
            d.ipfsCID,
            d.contentHash,
            d.approvalStatus,
            d.approvedBy,
            d.timestamp
        );
    }


    /**
     * @notice Get the latest approved version of a document.
     *
     * @dev Searches backwards from the latest version.
     *      This is appropriate for the prototype and keeps the
     *      blockchain as the authoritative source for approval state.
     */
    function getLatestApprovedDocumentVersion(
        string memory _documentId
    )
        public
        view
        returns (
            uint256 version,
            uint256 previousVersion,
            string memory ipfsCID,
            bytes32 contentHash,
            string memory approvalStatus,
            string memory approvedBy,
            uint256 timestamp
        )
    {
        require(
            documents[_documentId].exists,
            "UniversityRegistry: document not found"
        );

        uint256 latest = documents[_documentId].latestVersion;

        for (uint256 i = latest; i > 0; i--) {

            DocumentVersion memory d =
                documentVersions[_documentId][i];

            if (
                keccak256(
                    bytes(d.approvalStatus)
                ) ==
                keccak256(
                    bytes("APPROVED")
                )
            ) {
                return (
                    d.version,
                    d.previousVersion,
                    d.ipfsCID,
                    d.contentHash,
                    d.approvalStatus,
                    d.approvedBy,
                    d.timestamp
                );
            }
        }

        revert(
            "UniversityRegistry: no approved version"
        );
    }


    /**
     * @notice Return the complete version history of a document.
     */
    function getDocumentHistory(
        string memory _documentId
    )
        public
        view
        returns (DocumentVersion[] memory)
    {
        require(
            documents[_documentId].exists,
            "UniversityRegistry: document not found"
        );

        uint256 totalVersions =
            documents[_documentId].latestVersion;

        DocumentVersion[] memory history =
            new DocumentVersion[](totalVersions);

        for (uint256 i = 1; i <= totalVersions; i++) {
            history[i - 1] =
                documentVersions[_documentId][i];
        }

        return history;
    }


    /**
     * @notice Return the document-level metadata.
     */
    function getDocumentDetails(
        string memory _documentId
    )
        public
        view
        returns (
            string memory documentId,
            string memory studentId,
            string memory documentType,
            uint256 latestVersion,
            bool exists
        )
    {
        require(
            documents[_documentId].exists,
            "UniversityRegistry: document not found"
        );

        DocumentRecord memory d =
            documents[_documentId];

        return (
            d.documentId,
            d.studentId,
            d.documentType,
            d.latestVersion,
            d.exists
        );
    }


    // ============================================================
    // Existing Student View Functions
    // ============================================================

    function getStudentDetails(
        string memory _studentId
    )
        public
        view
        studentExists(_studentId)
        returns (
            string memory studentId,
            string memory name,
            string memory program,
            uint8 year,
            string memory documentsIPFSHash,
            bytes32 contentHash,
            uint256 timestamp,
            bool isActive
        )
    {
        StudentRecord memory s = students[_studentId];

        return (
            s.studentId,
            s.name,
            s.program,
            s.year,
            s.documentsIPFSHash,
            s.contentHash,
            s.timestamp,
            s.isActive
        );
    }


    /// @notice Return all semester IPFS hashes for a student.
    function getStudentSemesterHashes(
        string memory _studentId
    )
        public
        view
        studentExists(_studentId)
        returns (string[] memory)
    {
        return students[_studentId].semesterHashes;
    }


    /// @notice Return all content hashes for a student.
    function getStudentContentHashes(
        string memory _studentId
    )
        public
        view
        studentExists(_studentId)
        returns (bytes32[] memory)
    {
        return students[_studentId].contentHashes;
    }


    /// @notice Return all timestamps for a student.
    function getStudentTimestamps(
        string memory _studentId
    )
        public
        view
        studentExists(_studentId)
        returns (uint256[] memory)
    {
        return students[_studentId].timestamps;
    }


    function getStudentCount()
        public
        view
        returns (uint256)
    {
        return studentCount;
    }


    function getAllStudentIds()
        public
        view
        returns (string[] memory)
    {
        return studentIds;
    }


    function studentRecordExists(
        string memory _studentId
    )
        public
        view
        returns (bool)
    {
        return bytes(
            students[_studentId].studentId
        ).length > 0;
    }
}