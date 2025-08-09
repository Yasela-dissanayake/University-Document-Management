pragma solidity ^0.8.28;

contract UniversityRegistry {
    struct StudentRecord {
        string studentId;
        string name;
        string program;
        uint8 year;
        string documentsIPFSHash; // Latest document (for quick access)
        bytes32 contentHash; // Latest content hash (for quick verify)
        uint256 timestamp; // Latest record timestamp
        bool isActive;
        address studentWallet;
        string[] semesterHashes; // VERSIONED: all transcript/doc hashes
        bytes32[] contentHashes; // VERSIONED: all content hashes
        uint256[] timestamps; // VERSIONED: record per append/add
    }

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

    // ===== Events =====
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

    // ===== Modifiers =====

    modifier studentExists(string memory _studentId) {
        require(
            bytes(students[_studentId].studentId).length > 0,
            "Student not found"
        );
        _;
    }
    modifier onlyAuthorized() {
        // For demo: allow any sender. In production, restrict appropriately.
        _;
    }

    // ===== Core Functions =====

    /// Register a new student with initial transcript/hash (first semester)
    function registerStudent(
        string memory _studentId,
        string memory _name,
        string memory _program,
        uint8 _year,
        string memory _documentsIPFSHash,
        bytes32 _contentHash,
        address _studentWallet
    ) public onlyAuthorized {
        require(
            bytes(students[_studentId].studentId).length == 0,
            "Student already registered"
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

        // Initialize versioning arrays
        s.semesterHashes.push(_documentsIPFSHash);
        s.contentHashes.push(_contentHash);
        s.timestamps.push(block.timestamp);

        studentIds.push(_studentId);

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

        emit StudentRegistered(_studentId, _name, _program, _documentsIPFSHash);
    }

    /// Append a new semester record (transcript hash and content hash) for a student
    function addSemesterRecord(
        string memory _studentId,
        string memory _ipfsHash,
        bytes32 _contentHash
    ) public onlyAuthorized studentExists(_studentId) {
        StudentRecord storage s = students[_studentId];
        string memory oldHash = s.documentsIPFSHash;

        s.semesterHashes.push(_ipfsHash);
        s.contentHashes.push(_contentHash);
        s.timestamps.push(block.timestamp);

        // Update "current/latest" reference fields for compatibility
        s.documentsIPFSHash = _ipfsHash;
        s.contentHash = _contentHash;
        s.timestamp = block.timestamp;

        emit DocumentUpdated(_studentId, oldHash, _ipfsHash, msg.sender);
    }

    // ===== View/Getters =====

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

    /// Return all semester IPFS hashes for a student
    function getStudentSemesterHashes(
        string memory _studentId
    ) public view studentExists(_studentId) returns (string[] memory) {
        return students[_studentId].semesterHashes;
    }

    /// Optionally, add similar getters for contentHashes, timestamps

    function getStudentCount() public view returns (uint256) {
        return studentCount;
    }
    function getAllStudentIds() public view returns (string[] memory) {
        return studentIds;
    }
    function studentRecordExists(
        string memory _studentId
    ) public view returns (bool) {
        return bytes(students[_studentId].studentId).length > 0;
    }
}
